"""Durable folder provisioning, kept separate from project metadata edits."""
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from aiohttp import web

TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
MARKER = '.llms-creation'


class LostLease(Exception):
    pass


class ProjectCreation:
    def __init__(self, ctx, projects, provider):
        self.ctx, self.projects, self.provider = ctx, projects, provider
        self.owner = str(uuid.uuid4())
        self.tasks, self.events, self.users = {}, {}, set()
        self.slots = asyncio.Semaphore(2)
        self.stopping = False

    def db(self, user):
        root = Path(self.ctx.get_user_path(user), 'projects')
        root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(root / '.creation.sqlite', timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS operation (
            id TEXT PRIMARY KEY, requestId TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
            payload TEXT NOT NULL, name TEXT NOT NULL, destination TEXT NOT NULL,
            state TEXT NOT NULL, message TEXT NOT NULL, percent INTEGER, revision INTEGER NOT NULL DEFAULT 1,
            created REAL NOT NULL, updated REAL NOT NULL, owner TEXT, lease REAL NOT NULL DEFAULT 0,
            cancelled INTEGER NOT NULL DEFAULT 0, prepared INTEGER NOT NULL DEFAULT 0,
            child INTEGER, temporary TEXT, result TEXT, error TEXT)''')
        if 'temporary' not in {row[1] for row in db.execute('PRAGMA table_info(operation)')}:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if 'temporary' not in {row[1] for row in db.execute('PRAGMA table_info(operation)')}:
                    db.execute('ALTER TABLE operation ADD COLUMN temporary TEXT')
                    db.execute("UPDATE operation SET temporary='.create-' || id")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS reserved_destination ON operation(destination) WHERE state NOT IN ('succeeded','failed','cancelled','interrupted')")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS reserved_name ON operation(name) WHERE state NOT IN ('succeeded','failed','cancelled','interrupted')")
        return db

    def read(self, user, operation_id):
        with contextlib.closing(self.db(user)) as db:
            row = db.execute('SELECT * FROM operation WHERE id=?', (operation_id,)).fetchone()
        if row is None:
            raise web.HTTPNotFound(text='Project creation not found.')
        return dict(row)

    def snapshot(self, row):
        payload = json.loads(row['payload'])
        return {key: row[key] for key in ('id', 'state', 'message', 'percent', 'revision', 'created', 'error')} | {
            'project': payload['project'], 'source': payload['source'],
            'result': json.loads(row['result']) if row['result'] else None,
            'cancellable': row['state'] in ('queued', 'running') and not row['cancelled'],
        }

    def update(self, user, operation_id, *, _owner=None, **changes):
        changes['updated'] = time.time()
        with contextlib.closing(self.db(user)) as db, db:
            sql = 'UPDATE operation SET ' + ','.join(key + '=?' for key in changes) + ',revision=revision+1 WHERE id=?'
            params = (*changes.values(), operation_id)
            if _owner:
                sql += ' AND owner=?'
                params += (_owner,)
            if not db.execute(sql, params).rowcount:
                raise LostLease
        event = self.events.get((user, operation_id))
        if event:
            event.set()

    def validate(self, user, data):
        if not isinstance(data, dict) or not isinstance(data.get('project'), dict):
            raise web.HTTPBadRequest(text='Enter project details.')
        project = data['project']
        name, folder = project.get('name'), project.get('folder')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise web.HTTPBadRequest(text='Enter a project name (up to 200 characters).')
        if (not isinstance(folder, str) or not folder.strip() or len(folder) > 150
                or any(c in folder for c in '/\\:*?"<>|') or any(ord(c) < 32 for c in folder)
                or folder.startswith('.') or folder.endswith(('.', ' '))):
            raise web.HTTPBadRequest(text='Use a folder name without slashes or special path characters.')
        folder = folder.strip()
        if folder.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
            raise web.HTTPBadRequest(text='Choose a different folder name.')
        project = {'id': str(uuid.uuid4()), 'name': name.strip(), 'folder': folder,
                   'showInSidebar': project.get('showInSidebar', True),
                   'description': project.get('description', ''), 'publish': project.get('publish', '')}
        if type(project['showInSidebar']) is not bool or any(not isinstance(project[k], str) or len(project[k]) > 4000 for k in ('description', 'publish')):
            raise web.HTTPBadRequest(text='Invalid project details.')
        destination = self.projects.creation_destination(project, user)
        source = data.get('source', {'kind': 'new'})
        if not isinstance(source, dict) or source.get('kind') not in ('new', 'clone'):
            raise web.HTTPBadRequest(text='Choose New project or Clone repository.')
        provider = self.provider()
        if source['kind'] == 'clone':
            if provider is None:
                raise web.HTTPServiceUnavailable(text='Git cloning is not available on this server.')
            source = provider.validate(source)
            project['gitSource'] = source
        else:
            initialize = source.get('initializeGit', False)
            if type(initialize) is not bool:
                raise web.HTTPBadRequest(text='Invalid Git initialization option.')
            if initialize and provider is None:
                raise web.HTTPServiceUnavailable(text='Git is not available. Uncheck Initialize Git repository to continue.')
            source = {'kind': 'new', 'initializeGit': initialize}
        request_id = data.get('requestId')
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise web.HTTPBadRequest(text='Invalid creation request. Refresh and try again.') from None
        payload = {'project': project, 'source': source}
        fingerprint = {**payload, 'project': {k: v for k, v in project.items() if k != 'id'}}
        fingerprint = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        return request_id, fingerprint, payload, destination

    async def create(self, user, data):
        request_id, fingerprint, payload, destination = self.validate(user, data)
        with contextlib.closing(self.db(user)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM operation WHERE requestId=?', (request_id,)).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise web.HTTPConflict(text='This request was already submitted with different project details.')
                row = dict(existing)
            else:
                self.projects.check_creation(payload['project'], user)
                if os.path.lexists(destination):
                    raise web.HTTPConflict(text='That folder already exists. Choose another folder name.')
                now, operation_id = time.time(), str(uuid.uuid4())
                pending = db.execute("SELECT count(*) FROM operation WHERE state NOT IN ('succeeded','failed','cancelled','interrupted')").fetchone()[0]
                if pending >= 2:
                    raise web.HTTPConflict(text='Two projects are already being created. Wait for one to finish.')
                try:
                    db.execute('''INSERT INTO operation
                        (id,requestId,fingerprint,payload,name,destination,state,message,created,updated,temporary)
                        VALUES (?,?,?,?,?,?,'queued','Preparing your project…',?,?,?)''',
                               (operation_id, request_id, fingerprint, json.dumps(payload),
                                payload['project']['name'], os.path.normcase(destination), now, now, '.create-' + operation_id))
                except sqlite3.IntegrityError:
                    raise web.HTTPConflict(text='A project with that name or folder is already being created.') from None
                row = dict(db.execute('SELECT * FROM operation WHERE id=?', (operation_id,)).fetchone())
        self.schedule(user, row)
        return self.snapshot(row)

    def schedule(self, user, row):
        self.users.add(user)
        key = (user, row['id'])
        if not self.stopping and row['state'] not in TERMINAL and key not in self.tasks:
            task = asyncio.create_task(self.run(user, row['id']))
            self.tasks[key] = task
            def finished(task):
                if self.tasks.get(key) is task:
                    self.tasks.pop(key, None)
            task.add_done_callback(finished)

    async def recover(self, user):
        self.users.add(user)
        with contextlib.closing(self.db(user)) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM operation WHERE state NOT IN ('succeeded','failed','cancelled','interrupted')")]
        for row in rows:
            self.schedule(user, row)

    async def heartbeat(self, user, operation_id):
        while True:
            await asyncio.sleep(5)
            with contextlib.closing(self.db(user)) as db, db:
                db.execute('UPDATE operation SET lease=? WHERE id=? AND owner=?',
                           (time.time() + 20, operation_id, self.owner))

    async def run(self, user, operation_id):
        heartbeat = None
        async with self.slots:
            # Another process may own the operation. Wait without running a second clone.
            while not self.stopping:
                with contextlib.closing(self.db(user)) as db, db:
                    db.execute('BEGIN IMMEDIATE')
                    row = dict(db.execute('SELECT * FROM operation WHERE id=?', (operation_id,)).fetchone())
                    if row['state'] in TERMINAL:
                        return
                    if row['lease'] < time.time():
                        db.execute('UPDATE operation SET owner=?,lease=? WHERE id=?',
                                   (self.owner, time.time() + 20, operation_id))
                        break
                await asyncio.sleep(1)
            else:
                return
            payload = json.loads(row['payload'])
            project, source = payload['project'], payload['source']
            root = Path(self.ctx.get_user_path(user), 'projects')
            temporary = root / row['temporary']
            def write(**changes):
                self.update(user, operation_id, _owner=self.owner, **changes)
            try:
                heartbeat = asyncio.create_task(self.heartbeat(user, operation_id))
                if row['state'] == 'running' and not row['prepared']:
                    # A lost worker might still have a child writing its isolated temporary
                    # directory. Never reuse or clean it on this restart path.
                    write(state='interrupted', error='Creation was interrupted. Retry to start again safely.',
                                message='Creation interrupted', owner=None, lease=0)
                    return
                if row['cancelled']:
                    raise asyncio.CancelledError
                if not row['prepared']:
                    write(state='running', message='Cloning repository…' if source['kind'] == 'clone' else 'Creating your project…')
                    temporary.mkdir()
                    (temporary / MARKER).write_text(operation_id)
                    work = temporary / 'workspace'
                    if source['kind'] == 'clone' or source.get('initializeGit'):
                        provider = self.provider()
                        if provider is None:
                            raise RuntimeError('Git is no longer available. Retry after enabling the Git extension.')
                        last_progress = [None]

                        def progress(label, percent):
                            if (label, percent) != last_progress[0]:
                                last_progress[0] = (label, percent)
                                write(message=label + '…', percent=percent)

                        await provider.provision(str(work), source, progress,
                                                 lambda: bool(self.read(user, operation_id)['cancelled']),
                                                 lambda pid: write(child=pid))
                    else:
                        work.mkdir()
                    if self.read(user, operation_id)['cancelled']:
                        raise asyncio.CancelledError
                    if os.path.lexists(work / MARKER):
                        raise web.HTTPConflict(text='The repository contains a reserved project metadata file.')
                    (work / MARKER).write_text(operation_id)
                    write(state='finalizing', prepared=1,
                                message='Opening your project…', percent=None)
                if self.read(user, operation_id)['owner'] != self.owner:
                    raise LostLease
                result = self.projects.register_created(project, user, str(temporary / 'workspace'), operation_id, MARKER)
                write(state='succeeded', result=json.dumps(result), error=None,
                            message='Your project is ready', owner=None, lease=0)
                self.cleanup(temporary, operation_id)
            except LostLease:
                return
            except asyncio.CancelledError:
                state = 'interrupted' if self.stopping else 'cancelled'
                write(state=state, message='Creation interrupted' if self.stopping else 'Creation cancelled',
                            error='The server restarted. Retry to continue.' if self.stopping else None, owner=None, lease=0)
                self.cleanup(temporary, operation_id)
            except Exception as exc:
                error = exc.text if isinstance(exc, web.HTTPException) else str(exc)
                # Filesystem errors may contain paths; remote diagnostics never escape
                # the provisioner. Expose only the actionable, bounded message.
                if isinstance(exc, OSError):
                    error = 'Unable to create the project folder. Check available space and folder permissions.'
                write(state='failed', message='Project could not be created', error=error[:500], owner=None, lease=0)
                if not self.read(user, operation_id)['prepared']:
                    self.cleanup(temporary, operation_id)
            finally:
                if heartbeat:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat

    @staticmethod
    def cleanup(temporary, operation_id):
        marker = temporary / MARKER
        if not temporary.is_symlink() and not marker.is_symlink() and marker.is_file() and marker.read_text() == operation_id:
            shutil.rmtree(temporary)

    async def retry(self, user, operation_id):
        row = self.read(user, operation_id)
        if row['state'] not in ('failed', 'interrupted', 'cancelled'):
            return self.snapshot(row)
        temporary = Path(self.ctx.get_user_path(user), 'projects', row['temporary'])
        if not row['prepared'] and temporary.exists():
            if row['child']:
                try:
                    os.kill(row['child'], 0)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    raise web.HTTPConflict(text='The previous Git process is still finishing. Retry shortly.') from None
                else:
                    raise web.HTTPConflict(text='The previous Git process is still finishing. Retry shortly.')
            # A process may have died between spawning Git and recording its PID.
            # Interrupted attempts keep their isolated folder; the retry uses a new
            # path, so an orphan child can never write into the replacement clone.
            if row['state'] != 'interrupted':
                self.cleanup(temporary, operation_id)
        payload = json.loads(row['payload'])
        if not row['prepared']:
            self.projects.check_creation(payload['project'], user)
            if os.path.lexists(self.projects.creation_destination(payload['project'], user)):
                raise web.HTTPConflict(text='That folder already exists. Choose another folder name.')
        try:
            self.update(user, operation_id, state='queued', cancelled=0, owner=None, lease=0, child=None,
                        temporary=row['temporary'] if row['prepared'] else '.create-' + str(uuid.uuid4()),
                        error=None, percent=None, message='Preparing your project…')
        except sqlite3.IntegrityError:
            raise web.HTTPConflict(text='Another project is being created with that name or folder.') from None
        row = self.read(user, operation_id)
        self.schedule(user, row)
        return self.snapshot(row)

    async def start(self):
        parent = Path(self.ctx.get_user_path()).parent
        for db in parent.glob('*/projects/.creation.sqlite'):
            user = db.parent.parent.name
            await self.recover(None if user == 'default' else user)

    async def close(self):
        self.stopping = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def install_creation(ctx):
    def provider():
        if 'git' in getattr(ctx, 'config', {}).get('disable_extensions', []):
            return None
        return getattr(ctx.app, 'git_provisioner', None)

    creation = ProjectCreation(ctx, ctx.projects, provider)

    async def options(request):
        user = ctx.get_username(request)
        await creation.recover(user)
        git = provider()
        with contextlib.closing(creation.db(user)) as db:
            rows = db.execute('SELECT * FROM operation ORDER BY created DESC LIMIT 5').fetchall()
        return web.json_response({'create': True, 'initializeGit': git is not None, 'clone': git is not None,
                                  'root': os.path.join(ctx.get_user_path(user), 'projects'),
                                  'operations': [creation.snapshot(dict(row)) for row in rows]})

    async def create(request):
        result = await creation.create(ctx.get_username(request), await request.json())
        return web.json_response(result, status=202)

    async def operation(request):
        user, operation_id = ctx.get_username(request), request.match_info['id']
        row = creation.read(user, operation_id)
        creation.schedule(user, row)
        revision = request.query.get('revision')
        if revision == str(row['revision']) and row['state'] not in TERMINAL:
            event = creation.events.setdefault((user, operation_id), asyncio.Event())
            deadline = asyncio.get_running_loop().time() + 25
            while row['state'] not in TERMINAL and revision == str(row['revision']):
                event.clear()
                if asyncio.get_running_loop().time() >= deadline:
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), 1)
                row = creation.read(user, operation_id)
        return web.json_response(creation.snapshot(row))

    async def cancel(request):
        user, operation_id = ctx.get_username(request), request.match_info['id']
        with contextlib.closing(creation.db(user)) as db, db:
            changed = db.execute("UPDATE operation SET cancelled=1,revision=revision+1 WHERE id=? AND state IN ('queued','running')", (operation_id,)).rowcount
        row = creation.read(user, operation_id)
        if not changed and row['state'] not in TERMINAL:
            raise web.HTTPConflict(text='Your project is almost ready. Please wait for creation to finish.')
        creation.schedule(user, row)
        return web.json_response(creation.snapshot(row))

    async def retry(request):
        return web.json_response(await creation.retry(ctx.get_username(request), request.match_info['id']), status=202)

    ctx.add_get('creation/options', options)
    ctx.add_post('create', create)
    ctx.add_get('creation/operations/{id}', operation)
    ctx.add_post('creation/operations/{id}/cancel', cancel)
    ctx.add_post('creation/operations/{id}/retry', retry)
    ctx.register_startup_handler(creation.start)
    ctx.register_cleanup_handler(creation.close)
    return creation
