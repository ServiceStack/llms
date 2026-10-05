"""Explicit, scoped local repository mutations and the shared hook-free Git runner."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

from aiohttp import web
from llms.extensions.projects.explorer import within
from llms.workspace_operations import operation_lock, workspace_submission_lock


def git(repo, *args, local=True, identity=None, index_file=None, config=(), environment=None, timeout=30):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_LITERAL_PATHSPECS='1', GIT_NO_LAZY_FETCH='1', GIT_OPTIONAL_LOCKS='0')
    if not local:
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    if identity:
        env.update(GIT_AUTHOR_NAME=identity['name'], GIT_COMMITTER_NAME=identity['name'],
                   GIT_AUTHOR_EMAIL=identity['email'], GIT_COMMITTER_EMAIL=identity['email'])
    if index_file:
        env['GIT_INDEX_FILE'] = index_file
    if environment:
        env.update(environment)
    command = ['git', '--no-pager', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=' + os.devnull,
               '-c', 'commit.gpgSign=false', *[part for value in config for part in ('-c', value)], '-C', repo, *args]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                          start_new_session=os.name != 'nt') as process:
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if os.name != 'nt':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                if process.poll() is None:
                    process.kill()
            process.communicate()
            raise web.HTTPRequestTimeout(text='Git took too long. Refresh the repository before retrying.') from exc
        return subprocess.CompletedProcess(command, process.returncode, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace'))


def checked(repo, *args, strip=True, **kwargs):
    result = git(repo, *args, **kwargs)
    if result.returncode:
        raise web.HTTPConflict(text='Git could not complete this operation. Refresh and check the repository for conflicts or a locked index.')
    return result.stdout.strip() if strip else result.stdout


def repository_state(repo, local=True):
    head = git(repo, 'rev-parse', '--verify', 'HEAD', local=local)
    index = Path(repo, '.git', 'index')
    digest = hashlib.sha256(index.read_bytes() if index.exists() else b'').hexdigest()
    identity = {key: git(repo, 'config', '--get', 'user.' + key, local=local).stdout.strip() for key in ('name', 'email')}
    return {'indexRevision': (head.stdout.strip() if not head.returncode else '') + ':' + digest,
            'identity': identity}


def worktree_revision(repo, filename):
    try:
        stat = os.lstat(os.path.join(repo, filename))
        state = [stat.st_mode, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_dev]
    except FileNotFoundError:
        state = None
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()


def workspace_revision(repo, index_revision, local=True):
    result = git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all', local=local)
    if result.returncode:
        raise web.HTTPConflict(text='Unable to review working changes. Refresh the repository.')
    digest = hashlib.sha256((index_revision + result.stdout).encode())
    records = iter(result.stdout.split('\0'))
    for record in records:
        if not record:
            continue
        paths = [record[3:]]
        if record[0] in 'RC' or record[1] in 'RC':
            paths.append(next(records, ''))
        for filename in paths:
            digest.update(worktree_revision(repo, filename).encode())
    return digest.hexdigest()


@contextmanager
def temporary_index(repo):
    with tempfile.NamedTemporaryFile(prefix='llms-index-', dir=os.path.join(repo, '.git'), delete=False) as stream:
        filename = stream.name
        index = Path(repo, '.git', 'index')
        if index.exists():
            stream.write(index.read_bytes())
    if not index.exists():
        Path(filename).unlink()
    try:
        yield filename
    finally:
        Path(filename).unlink(missing_ok=True)


@contextmanager
def repository_index_lock(repo):
    lock = Path(repo, '.git', 'index.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise web.HTTPConflict(text='Another Git operation has locked the index. Wait for it to finish and refresh.') from exc
    try:
        yield
    finally:
        os.close(descriptor)
        lock.unlink()


def write_tree_snapshot(repo, local):
    # Hold the normal index lock while committing an isolated copy of the reviewed index.
    # Git's cache-tree writes then affect only this temporary file.
    with tempfile.NamedTemporaryFile(prefix='llms-index-', dir=os.path.join(repo, '.git'), delete=False) as stream:
        filename = stream.name
        stream.write(Path(repo, '.git', 'index').read_bytes())
    try:
        return checked(repo, 'write-tree', local=local, index_file=filename)
    finally:
        Path(filename).unlink()


def validate_repo(roots, path):
    from . import git_directory
    roots, path, root, repo = git_directory(roots, path)
    if not repo:
        raise web.HTTPBadRequest(text='This directory has no Git repository')
    metadata = Path(repo, '.git')
    if metadata.is_symlink() or not metadata.is_dir() or not within(os.path.realpath(metadata), repo):
        raise web.HTTPBadRequest(text='Linked worktrees and external Git directories are not supported for repository changes yet.')
    top = checked(repo, 'rev-parse', '--show-toplevel')
    common = checked(repo, 'rev-parse', '--git-common-dir')
    canonical = lambda value: os.path.normcase(os.path.realpath(value))
    if canonical(top) != canonical(repo) or canonical(os.path.join(repo, common)) != canonical(metadata):
        raise web.HTTPForbidden(text='Git metadata is outside the repository')
    critical = ['config', 'index', 'HEAD', 'FETCH_HEAD', 'ORIG_HEAD', 'packed-refs', 'refs/stash', 'logs/refs/stash', 'objects', 'objects/info', 'objects/pack',
                'refs', 'logs', 'llms-locks', 'llms-commits']
    critical += ['objects/' + format(i, '02x') for i in range(256)]
    branch = git(repo, 'symbolic-ref', '--quiet', 'HEAD')
    if not branch.returncode:
        reference = branch.stdout.strip()
        if not reference.startswith('refs/'):
            raise web.HTTPBadRequest(text='Unsupported Git reference')
        critical += [reference, 'logs/' + reference]
    for relative in critical:
        target = metadata / relative
        if target.is_symlink() or not within(os.path.realpath(target), str(metadata)):
            raise web.HTTPForbidden(text='Git metadata links outside the repository are not supported')
    for marker in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'BISECT_LOG'):
        if (metadata / marker).exists():
            raise web.HTTPConflict(text='Finish the merge, rebase, or other Git operation before changing this repository.')
    if checked(repo, 'ls-files', '--unmerged', '-z'):
        raise web.HTTPConflict(text='Resolve merge conflicts before staging or committing.')
    return roots, path, root, repo


def validate_paths(repo, root, paths):
    if not isinstance(paths, list) or not paths or len(paths) > 10000 or any(not isinstance(p, str) for p in paths):
        raise web.HTTPBadRequest(text='Select files to change')
    for filename in paths:
        if not filename or '\0' in filename or os.path.isabs(filename) or '..' in filename.replace('\\', '/').split('/'):
            raise web.HTTPBadRequest(text='Invalid repository path')
        target = os.path.realpath(os.path.join(repo, filename))
        if not within(target, root) or not within(target, repo) or within(target, os.path.join(repo, '.git')):
            raise web.HTTPForbidden(text='File is outside the allowed repository files')
    return list(dict.fromkeys(paths))


def active_run(db, user, project_id, repo):
    if db is None:
        return False
    runs = db.db.all("SELECT * FROM agent_run WHERE status IN ('queued','running','waiting_approval')")
    for run in runs:
        thread = db.get_thread(run['threadId'], user=run.get('user'))
        if run.get('user') == user and thread and (thread.get('projectId') or None) == (project_id or None):
            return True
        workspace = run.get('workspace') or {}
        if isinstance(workspace, str):
            workspace = json.loads(workspace)
        if any(within(repo, directory) or within(directory, repo) for directory in workspace.get('directories', [])):
            return True
    return False


def mutate(roots, path, action, body, *, data_root, user=None, project_id=None, db=None, local=True):
    # The submission gate is acquired before checking runs, and held until Git finishes.
    with workspace_submission_lock(data_root):
        roots, path, root, repo = validate_repo(roots, path)
        with operation_lock(os.path.join(repo, '.git', 'llms-locks'), 'repository'):
            if active_run(db, user, project_id, repo):
                raise web.HTTPConflict(text='An agent is active in this workspace. Wait for it to finish before changing Git state.')
            from . import browse_git
            data = browse_git(roots, path, local=local)
            if action in ('stage-all', 'unstage-all', 'discard-all', 'undo', 'stash', 'stash-untracked', 'stash-staged', 'stash-apply', 'stash-pop'):
                from .repository import repository_operation
                return repository_operation(repo, root, data, action, body, local)
            if action == 'discard':
                return discard_file(repo, root, data, body, local)
            if action in ('stage', 'unstage'):
                paths = validate_paths(repo, root, body.get('paths'))
                entries = data['changes'] if action == 'stage' else data['stagedChanges']
                available = {entry['relativePath']: entry for entry in entries}
                if any(p not in available for p in paths):
                    raise web.HTTPConflict(text='The selected files have changed. Refresh the repository and select them again.')
                paths += [available[p]['oldRelativePath'] for p in paths[:] if available[p].get('oldRelativePath')]
                paths = validate_paths(repo, root, paths)
                if action == 'stage':
                    attributes = checked(repo, 'check-attr', '-z', 'filter', '--', *paths, local=local).split('\0')
                    if any(value not in ('unspecified', 'unset') for value in attributes[2::3]):
                        raise web.HTTPBadRequest(text='These files use a Git content filter. Stage them with your Git client instead.')
                    checked(repo, 'add', '--', *paths, local=local)
                elif git(repo, 'rev-parse', '--verify', 'HEAD', local=local).returncode:
                    checked(repo, 'rm', '--cached', '-r', '-f', '--', *paths, local=local)
                else:
                    checked(repo, 'restore', '--staged', '--', *paths, local=local)
                return {'operation': action}
            if action not in ('commit', 'commit-all'):
                raise web.HTTPBadRequest(text='Unknown repository operation')
            with repository_index_lock(repo):
                return commit_index(repo, root, roots, path, body, local, user, all_changes=action == 'commit-all')


def discard_file(repo, root, data, body, local):
    paths = validate_paths(repo, root, body.get('paths'))
    if len(paths) != 1:
        raise web.HTTPBadRequest(text='Select one unstaged file to discard')
    filename = paths[0]
    entry = next((item for item in data['changes'] if item['relativePath'] == filename), None)
    if (not entry or body.get('indexRevision') != data['indexRevision']
            or body.get('worktreeRevision') != worktree_revision(repo, filename)):
        raise web.HTTPConflict(text='The file or staged changes changed. Refresh and review them before discarding.')
    target = Path(repo, filename)
    # Discard applies to one file; never recurse into a directory, submodule, or linked path.
    for parent in (target, *target.parents):
        if parent == Path(repo):
            break
        if parent.is_symlink():
            raise web.HTTPBadRequest(text='Discard linked files with your Git client instead.')
    if target.is_dir():
        raise web.HTTPBadRequest(text='Discard directories and submodules with your Git client instead.')
    with repository_index_lock(repo):
        if (body.get('indexRevision') != repository_state(repo, local)['indexRevision']
                or body.get('worktreeRevision') != worktree_revision(repo, filename)):
            raise web.HTTPConflict(text='The file or staged changes changed. Refresh and review them before discarding.')
        if entry['status'] == '?':
            target.unlink()
        else:
            index = checked(repo, 'ls-files', '--stage', '-z', '--', filename, local=local)
            records = [row.split('\t', 1) for row in index.split('\0') if '\t' in row]
            if len(records) != 1 or records[0][1] != filename or records[0][0].split()[0] not in ('100644', '100755'):
                raise web.HTTPBadRequest(text='Discard this file with your Git client instead.')
            attributes = checked(repo, 'check-attr', '-z', 'filter', '--', filename, local=local).split('\0')
            if any(value not in ('unspecified', 'unset') for value in attributes[2::3]):
                raise web.HTTPBadRequest(text='This file uses a Git content filter. Discard it with your Git client instead.')
            # Without --index, checkout-index restores only this worktree file while our index lock stays held.
            checked(repo, 'checkout-index', '--force', '--', filename, local=local, config=['submodule.recurse=false'])
    return {'operation': 'discard'}


def commit_index(repo, root, roots, path, body, local, user, all_changes=False):
    from . import browse_git
    state = repository_state(repo, local)
    data = browse_git(roots, path, local=local)
    message = body.get('message')
    request_id = body.get('requestId')
    if not isinstance(message, str) or not message.strip() or len(message) > 10000 or '\0' in message:
        raise web.HTTPBadRequest(text='Enter a commit message (up to 10,000 characters)')
    if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
        raise web.HTTPBadRequest(text='A commit request ID is required')
    message = message.strip()
    identity = body.get('identity') or state['identity']
    if not isinstance(identity, dict) or any(not isinstance(identity.get(k), str) or not identity[k].strip()
            or len(identity[k]) > 256 or any(c in identity[k] for c in '\n\r\0<>') for k in ('name', 'email')):
        raise web.HTTPBadRequest(text='Enter your Git author name and email')
    review = [user, message, identity, body.get('indexRevision')]
    if all_changes:
        review += ['all', body.get('workspaceRevision')]
    fingerprint = hashlib.sha256(json.dumps(review, sort_keys=True).encode()).hexdigest()
    records = Path(repo, '.git', 'llms-commits')
    records.mkdir(exist_ok=True)
    record = records / (hashlib.sha256((str(user) + request_id).encode()).hexdigest() + '.json')
    if record.is_symlink():
        raise web.HTTPForbidden(text='Invalid commit receipt')
    head = git(repo, 'rev-parse', '--verify', 'HEAD', local=local)
    previous = head.stdout.strip() if not head.returncode else ''
    if record.exists():
        receipt = json.loads(record.read_text())
        if receipt['fingerprint'] != fingerprint:
            raise web.HTTPConflict(text='This commit request was already used. Refresh before committing again.')
        revision = receipt['revision']
        if previous == revision or not git(repo, 'merge-base', '--is-ancestor', revision, 'HEAD', local=local).returncode:
            return {'operation': 'commit', 'revision': revision}
        if previous != receipt['previous']:
            raise web.HTTPConflict(text='The branch changed during the commit. Inspect Recent commits before retrying.')
    else:
        if body.get('indexRevision') != state['indexRevision']:
            raise web.HTTPConflict(text='Staged changes have changed. Refresh and review them before committing.')
        if all_changes:
            from .repository import check_review, stage_all
            check_review(repo, data, body, local)
            if not data['changes'] and not data['stagedChanges']:
                raise web.HTTPBadRequest(text='There are no changes to commit')
            with temporary_index(repo) as filename:
                stage_all(repo, root, local, index_file=filename)
                check_review(repo, data, body, local)
                os.replace(filename, Path(repo, '.git', 'index'))
        elif not data['stagedChanges']:
            raise web.HTTPBadRequest(text='Stage at least one change before committing')
        staged_paths = checked(repo, 'diff', '--cached', '--name-only', '--no-renames', '-z', '--', local=local, strip=False).split('\0')
        validate_paths(repo, root, [p for p in staged_paths if p])
        if body.get('saveIdentity') is True:
            for key in ('name', 'email'):
                checked(repo, 'config', '--local', 'user.' + key, identity[key], local=local)
        tree = write_tree_snapshot(repo, local)
        revision = checked(repo, 'commit-tree', tree, *(['-p', previous] if previous else []), '-m', message,
                           local=local, identity=identity)
        # Persist the exact object before updating HEAD, so a lost response is safe to retry.
        with tempfile.NamedTemporaryFile(mode='w', prefix='receipt-', dir=records, delete=False) as stream:
            temporary = stream.name
            json.dump({'fingerprint': fingerprint, 'revision': revision, 'previous': previous}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, record)
    checked(repo, 'update-ref', '-m', 'commit: ' + message.splitlines()[0], 'HEAD', revision,
            previous or '0' * len(revision), local=local)
    return {'operation': 'commit', 'revision': revision}
