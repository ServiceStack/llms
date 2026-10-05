"""Optional Git workspace view and project provisioning."""
import asyncio
import difflib
import os
import re
import shutil
import subprocess
import threading

from aiohttp import web
from llms.extensions.projects.explorer import resolve_directory, within

MAX_PREVIEW_BYTES = 1024 * 1024


class GitOutputTooLarge(ValueError):
    pass


def run_git(repo, *args, max_output_bytes=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    environment.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_LITERAL_PATHSPECS='1', GIT_NO_LAZY_FETCH='1')
    command = ['git', '--no-pager', '-c', 'core.fsmonitor=false', '-C', repo, *args]
    if max_output_bytes is None:
        return subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=5, env=environment)
    # Stop oversized aggregate diffs before buffering their entire contents in memory.
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment) as process:
        expired = threading.Event()
        def expire():
            expired.set()
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
        timer = threading.Timer(5, expire)
        timer.daemon = True
        timer.start()
        try:
            output = process.stdout.read(max_output_bytes + 1)
            if len(output) > max_output_bytes:
                process.kill()
                raise GitOutputTooLarge('Git output is too large')
            process.wait()
            if expired.is_set():
                raise subprocess.TimeoutExpired(command, 5)
            return subprocess.CompletedProcess(command, process.returncode, output.decode('utf-8', 'replace'), '')
        finally:
            timer.cancel()


def git_directory(roots, path=None):
    roots, path, root = resolve_directory(roots, path)
    if root is None:
        return roots, path, root, None
    repo = path
    # Never discover a repository above the user's allowed workspace.
    while not os.path.exists(os.path.join(repo, '.git')) and repo != root:
        repo = os.path.dirname(repo)
    if not os.path.exists(os.path.join(repo, '.git')):
        repo = None
    return roots, path, root, repo


def browse_git(roots, path=None, local=True):
    roots, path, root, repo = git_directory(roots, path)
    result = {'roots': roots, 'path': path, 'repository': repo, 'changes': [], 'stagedChanges': [], 'commits': []}
    if repo is None:
        return result

    try:
        status = run_git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
        if status.returncode:
            raise web.HTTPBadRequest(text='Git changes are unavailable')
        records = iter(status.stdout.split('\0'))
        for record in records:
            if not record:
                continue
            index_status, worktree_status = record[:2]
            filename = record[3:]
            original = None
            if index_status in 'RC' or worktree_status in 'RC':
                original = next(records, None)
            target = os.path.realpath(os.path.join(repo, filename))
            if not within(target, root):
                continue
            entry = {'name': os.path.basename(filename), 'relativePath': filename,
                     'path': os.path.join(repo, filename), 'oldRelativePath': original}
            if worktree_status != ' ' or index_status == 'U':
                result['changes'].append({**entry, 'status': '?' if record[:2] == '??' else worktree_status,
                                          'deleted': worktree_status == 'D',
                                          'oldRelativePath': original if worktree_status in 'RC' else None})
            if index_status not in (' ', '?'):
                result['stagedChanges'].append({**entry, 'status': index_status, 'deleted': index_status == 'D'})
        from .operations import repository_state, worktree_revision, workspace_revision
        result.update(repository_state(repo, local))
        result['workspaceRevision'] = workspace_revision(repo, result['indexRevision'], local)
        for entry in result['changes']:
            entry['worktreeRevision'] = worktree_revision(repo, entry['relativePath'])
        from .sync import sync_state
        result.update(sync_state(repo, local))
        from .repository import menu_state
        result.update(menu_state(repo, local))
        result['canCommit'] = os.path.isdir(os.path.join(repo, '.git')) and not os.path.islink(os.path.join(repo, '.git'))
        branch = run_git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        result['branch'] = branch.stdout.strip() if not branch.returncode else 'Detached HEAD'
        if not run_git(repo, 'rev-parse', '--verify', 'HEAD').returncode:
            proc = run_git(repo, 'log', '-50', '-z', '--format=%H%x00%h%x00%aI%x00%an%x00%ae%x00%s%x00%b%x00%D%x00%B')
            if proc.returncode:
                raise web.HTTPBadRequest(text='Git history is unavailable')
            fields = proc.stdout.split('\0')
            result['commits'] = [dict(zip(('id', 'hash', 'date', 'author', 'email', 'subject', 'body', 'refs', 'message'), fields[i:i + 9]))
                                 for i in range(0, len(fields) - 1, 9)]
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise web.HTTPBadRequest(text='Git information is unavailable') from exc
    return result


def diff_git(roots, path=None, file=None, staged=False):
    if not file:
        raise web.HTTPBadRequest(text='Select a file to compare')
    roots, path, root, repo = git_directory(roots, path)
    if root is None:
        raise web.HTTPForbidden(text='No allowed workspace directories')
    target = os.path.abspath(file)
    if not within(os.path.realpath(target), root):
        raise web.HTTPForbidden(text='File is outside the workspace')
    if repo is None:
        raise web.HTTPBadRequest(text='This directory has no Git repository')
    if not within(target, repo):
        raise web.HTTPForbidden(text='File is outside the repository')
    relative = os.path.relpath(target, repo).replace(os.sep, '/')
    result = {'roots': roots, 'path': path, 'repository': repo,
              'file': {'path': target, 'name': os.path.basename(target), 'relativePath': relative}, 'patch': ''}

    def message(text):
        result['message'] = text
        return result

    try:
        index = run_git(repo, 'ls-files', '--stage', '-z', '--', relative)
        if index.returncode:
            raise web.HTTPBadRequest(text='Git index is unavailable')
        entries = [record.split('\t', 1)[0].split() for record in index.stdout.split('\0')
                   if '\t' in record and record.split('\t', 1)[1] == relative]
        submodule = any(entry[0] == '160000' for entry in entries)
        for mode, object_id, _stage in entries:
            if mode == '160000':
                continue
            size = run_git(repo, 'cat-file', '-s', object_id)
            if size.returncode:
                raise web.HTTPBadRequest(text='Git file is unavailable')
            if int(size.stdout) > MAX_PREVIEW_BYTES:
                return message('File is too large to compare (1 MiB limit).')
        if staged:
            content = b''
            head = run_git(repo, 'rev-parse', '--verify', 'HEAD')
            if not head.returncode:
                old = run_git(repo, 'ls-tree', '-l', '-z', 'HEAD', '--', relative)
                for record in old.stdout.split('\0'):
                    fields = record.split('\t', 1)[0].split()
                    if len(fields) == 4 and fields[1] == 'blob' and int(fields[3]) > MAX_PREVIEW_BYTES:
                        return message('File is too large to compare (1 MiB limit).')
        elif os.path.islink(target):
            content = os.readlink(target).encode('utf-8', errors='replace')
        elif os.path.isfile(target):
            with open(target, 'rb') as stream:
                content = stream.read(MAX_PREVIEW_BYTES + 1)
        elif (entries and not os.path.exists(target)) or submodule:
            content = b''  # Deleted paths can have nonexistent parent directories.
        else:
            raise web.HTTPNotFound(text='File is unavailable')
        if len(content) > MAX_PREVIEW_BYTES:
            return message('File is too large to compare (1 MiB limit).')
        if b'\0' in content:
            return message('Binary file differences cannot be displayed.')
        if entries or staged:
            proc = run_git(repo, 'diff', *(['--cached'] if staged else []), '--no-ext-diff', '--no-textconv', '--no-color',
                           '--unified=3', '--submodule=short', '--', relative)
            if proc.returncode:
                raise web.HTTPBadRequest(text='Git diff is unavailable')
            patch = proc.stdout
            if any(line.startswith('Binary files ') for line in patch.splitlines()):
                return message('Binary file differences cannot be displayed.')
            if any(line.startswith('@@@') for line in patch.splitlines()):
                return message('This file has merge conflicts. Resolve them before comparing unstaged changes.')
        else:
            # New files have no index version. Use a portable empty-file comparison.
            if not content:
                return message('New empty file. There are no lines to display.')
            lines = difflib.unified_diff([], content.decode('utf-8', errors='replace').splitlines(keepends=True),
                                         fromfile='/dev/null', tofile='b/' + relative)
            patch = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                            for line in lines)
        if len(patch.encode('utf-8')) > MAX_PREVIEW_BYTES or patch.count('\n') > 10000:
            return message('Diff is too large to preview. Open the file to inspect its contents.')
        result['patch'] = patch
        if not patch:
            return message('No staged differences.' if staged else 'No unstaged differences. The file may have changed since the list was refreshed.')
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise web.HTTPBadRequest(text='Git diff is unavailable') from exc


def commit_git(roots, path=None, commit=None):
    roots, path, root, repo = git_directory(roots, path)
    if repo is None:
        raise web.HTTPBadRequest(text='This directory has no Git repository')
    if not commit or not re.fullmatch(r'[0-9a-fA-F]{7,64}', commit):
        raise web.HTTPBadRequest(text='Select a valid commit hash')
    try:
        resolved = run_git(repo, 'rev-parse', '--verify', commit + '^{commit}')
        if resolved.returncode:
            raise web.HTTPNotFound(text='Commit is unavailable')
        commit_id = resolved.stdout.strip()
        parents = run_git(repo, 'rev-list', '--parents', '-n', '1', commit_id)
        if parents.returncode:
            raise web.HTTPBadRequest(text='Commit is unavailable')
        parent_ids = parents.stdout.split()[1:]
        # Merge commits compare against their first parent; root commits use the empty tree.
        comparison = ['diff', parent_ids[0], commit_id] if parent_ids else [
            'diff-tree', '--root', '--no-commit-id', '-r', commit_id]
        changed = run_git(repo, *comparison, '--name-status', '-z', '-M', '--')
        if changed.returncode:
            raise web.HTTPBadRequest(text='Commit files are unavailable')
        changes = []
        records = iter(changed.stdout.split('\0'))
        for status in records:
            if not status:
                continue
            previous = next(records, '')
            filename = next(records, '') if status[0] in 'RC' else previous
            target = os.path.join(repo, filename)
            old_target = os.path.join(repo, previous)
            if not filename or not all(within(os.path.realpath(p), root) for p in (target, old_target)):
                continue
            changes.append({'name': os.path.basename(filename), 'relativePath': filename,
                            'path': target, 'status': status[0], 'deleted': status[0] == 'D',
                            'oldRelativePath': previous if status[0] in 'RC' else None})
        return {'roots': roots, 'path': path, 'repository': repo, 'commit': commit_id,
                'parent': parent_ids[0] if parent_ids else None, 'changes': changes}
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise web.HTTPBadRequest(text='Commit files are unavailable') from exc


def diff_commit_git(roots, path=None, file=None, commit=None):
    result = commit_git(roots, path, commit)
    if not file:
        raise web.HTTPBadRequest(text='Select a file to compare')
    target = os.path.abspath(file)
    change = next((entry for entry in result['changes'] if os.path.normcase(entry['path']) == os.path.normcase(target)), None)
    if change is None:
        raise web.HTTPNotFound(text='File is not part of this commit or is outside the workspace')
    result['file'] = change
    result['patch'] = ''
    result.pop('changes')

    def message(text):
        result['message'] = text
        return result

    repo, commit_id, parent = result['repository'], result['commit'], result['parent']
    paths = list(dict.fromkeys([change['relativePath'], change['oldRelativePath'] or change['relativePath']]))
    try:
        for revision in filter(None, (parent, commit_id)):
            entries = run_git(repo, 'ls-tree', '-l', '-z', revision, '--', *paths)
            if entries.returncode:
                raise web.HTTPBadRequest(text='Commit file is unavailable')
            for entry in entries.stdout.split('\0'):
                if '\t' not in entry:
                    continue
                fields = entry.split('\t', 1)[0].split()
                if fields[1] == 'blob' and int(fields[3]) > MAX_PREVIEW_BYTES:
                    return message('File is too large to compare (1 MiB limit).')
        comparison = ['diff', parent, commit_id] if parent else ['diff-tree', '--root', '--no-commit-id', '-r', commit_id]
        diff = run_git(repo, *comparison, '-p', '-M', '--no-ext-diff', '--no-textconv', '--no-color', '--unified=3', '--', *paths)
        if diff.returncode:
            raise web.HTTPBadRequest(text='Commit diff is unavailable')
        if any(line.startswith('Binary files ') for line in diff.stdout.splitlines()):
            return message('Binary file differences cannot be displayed.')
        if len(diff.stdout.encode('utf-8')) > MAX_PREVIEW_BYTES or diff.stdout.count('\n') > 10000:
            return message('Diff is too large to preview.')
        result['patch'] = diff.stdout
        if not diff.stdout:
            return message('No line differences in this commit.')
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise web.HTTPBadRequest(text='Commit diff is unavailable') from exc


def install(ctx):
    if not shutil.which('git') or 'projects' in ctx.config.get('disable_extensions', []):
        ctx.disabled = True
        return

    from .provision import GitProvisioner
    from .messages import CommitMessageGenerator
    generator = CommitMessageGenerator(ctx)
    ctx.app.git_provisioner = GitProvisioner(shutil.which('git'), local=ctx.is_local,
                                           hosts=ctx.config.get('git_clone_hosts'))

    async def capabilities(request):
        return web.json_response({'initializeGit': True, 'clone': True, 'commit': True,
                                  'sync': True, 'githubPublish': False})

    ctx.add_get('capabilities', capabilities)

    async def respond(request, reader, *args):
        resolver = getattr(ctx.projects, 'resolve_explorer_workspace', None)
        if not callable(resolver):
            raise web.HTTPServiceUnavailable(text='Workspace browsing is unavailable')
        try:
            roots = resolver(request.query.get('projectId') or None, ctx.get_username(request),
                             is_admin=ctx.is_admin(request))['directories']
            result = await asyncio.to_thread(reader, roots, request.query.get('path'), *args)
            if reader is browse_git:
                result['canGenerateCommit'] = bool(generator.template())
            return web.json_response(result)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        except OSError as exc:
            raise web.HTTPBadRequest(text='Unable to read this directory') from exc

    async def workspace(request):
        return await respond(request, browse_git, ctx.is_local)

    async def diff(request):
        if request.query.get('commit'):
            return await respond(request, diff_commit_git, request.query.get('file'), request.query.get('commit'))
        return await respond(request, diff_git, request.query.get('file'), request.query.get('staged') == '1')

    async def commit(request):
        return await respond(request, commit_git, request.query.get('commit'))

    ctx.add_get('workspace', workspace)
    ctx.add_get('diff', diff)
    ctx.add_get('commit', commit)

    async def mutation(request):
        from .operations import mutate
        if not ctx.check_auth(request)[0]:
            return web.json_response(ctx.error_auth_required, status=401)
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text='Invalid repository operation')
        project_id = body.get('projectId') or None
        try:
            roots = ctx.projects.resolve_explorer_workspace(project_id, ctx.get_username(request),
                         is_admin=ctx.is_admin(request))['directories']
            result = await asyncio.to_thread(mutate, roots, body.get('path'), request.match_info['action'], body,
                data_root=ctx.get_user_path(), user=ctx.get_username(request), project_id=project_id,
                db=getattr(ctx.app, 'agent_db', None), local=ctx.is_local)
            return web.json_response(result)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        except OSError as exc:
            raise web.HTTPBadRequest(text='Unable to change this repository') from exc

    ctx.add_post('repositories/{action:stage|unstage|commit|commit-all|discard|stage-all|unstage-all|discard-all|undo|stash|stash-untracked|stash-staged|stash-apply|stash-pop}', mutation)

    async def sync(request):
        from .sync import sync_repository
        if not ctx.check_auth(request)[0]:
            return web.json_response(ctx.error_auth_required, status=401)
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text='Invalid repository operation')
        user = ctx.get_username(request)
        project_id = body.get('projectId') or None
        try:
            roots = ctx.projects.resolve_explorer_workspace(project_id, user,
                        is_admin=ctx.is_admin(request))['directories']
            result = await asyncio.to_thread(sync_repository, roots, body.get('path'), request.match_info['action'], body,
                data_root=ctx.get_user_path(), user=user, project_id=project_id,
                db=getattr(ctx.app, 'agent_db', None), local=ctx.is_local, hosts=ctx.config.get('git_clone_hosts'))
            return web.json_response(result)
        except OSError as exc:
            raise web.HTTPBadRequest(text='Unable to sync this repository') from exc

    ctx.add_post('repositories/{action:push|pull|sync}', sync)

    async def generate_message(request):
        from .messages import staged_diff
        if not ctx.check_auth(request)[0]:
            return web.json_response(ctx.error_auth_required, status=401)
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text='Invalid commit message request')
        user = ctx.get_username(request)
        try:
            roots = ctx.projects.resolve_explorer_workspace(body.get('projectId') or None, user,
                        is_admin=ctx.is_admin(request))['directories']
            snapshot = await asyncio.to_thread(staged_diff, roots, body.get('path'), body.get('indexRevision'))
            message = await generator.generate(snapshot, user)
            # Never offer a suggestion for an index that changed during the model request.
            from .operations import repository_state
            _, _, _, repo = git_directory(roots, body.get('path'))
            revision = (await asyncio.to_thread(repository_state, repo))['indexRevision'] if repo else None
            if revision != snapshot['indexRevision']:
                raise web.HTTPConflict(text='Staged changes changed while generating. Refresh and generate again.')
            return web.json_response({'message': message, 'indexRevision': revision})
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
        except OSError as exc:
            raise web.HTTPBadRequest(text='Unable to read staged changes') from exc

    ctx.add_post('repositories/message', generate_message)


__install__ = install
