"""Reviewed repository menu actions, sharing the mutation route's workspace and run locks."""
from pathlib import Path

from aiohttp import web
from .operations import checked, git, repository_index_lock, repository_state, validate_paths, workspace_revision


def menu_state(repo, local=True):
    stash = git(repo, 'rev-parse', '--verify', 'refs/stash^{commit}', local=local)
    head = git(repo, 'rev-parse', '--verify', 'HEAD', local=local)
    branch = git(repo, 'symbolic-ref', '--quiet', 'HEAD', local=local)
    published = checked(repo, 'for-each-ref', '--contains', head.stdout.strip(), '--format=%(refname)', 'refs/remotes', local=local) if not head.returncode else ''
    reason = ('Create a commit first' if head.returncode else 'Check out a branch before undoing a commit' if branch.returncode else
              'This commit is on a remote-tracking branch. Use your Git client to revert a shared commit.' if published else '')
    return {'stashId': stash.stdout.strip() if not stash.returncode else None, 'canUndo': not bool(reason), 'undoReason': reason}


def check_review(repo, data, body, local):
    state = repository_state(repo, local)
    if (body.get('branch') != data.get('branch') or body.get('head') != data.get('head')
            or body.get('indexRevision') != data['indexRevision'] or state['indexRevision'] != data['indexRevision']
            or body.get('workspaceRevision') != workspace_revision(repo, state['indexRevision'], local)):
        raise web.HTTPConflict(text='The repository changed. Refresh and review its changes before trying again.')


def paths_from(repo, *args, local=True):
    return [name for name in checked(repo, *args, '-z', '--', local=local, strip=False).split('\0') if name]


def changed_paths(repo, local):
    return list(dict.fromkeys(paths_from(repo, 'diff', '--name-only', '--no-renames', local=local)
        + paths_from(repo, 'diff', '--cached', '--name-only', '--no-renames', local=local)
        + paths_from(repo, 'ls-files', '--others', '--exclude-standard', local=local)))


def safe_paths(repo, root, paths, local, revisions=()):
    paths = validate_paths(repo, root, paths) if paths else []
    for filename in paths:
        target = Path(repo, filename)
        if target.is_dir():
            raise web.HTTPBadRequest(text='Use your Git client for changes involving directories or submodules.')
        for parent in (target, *target.parents):
            if parent == Path(repo):
                break
            if parent.is_symlink():
                raise web.HTTPBadRequest(text='Use your Git client for changes involving linked paths.')
    if paths:
        attributes = checked(repo, 'check-attr', '-z', 'filter', 'merge', '--', *paths, local=local).split('\0')
        for name, attribute, value in zip(attributes[0::3], attributes[1::3], attributes[2::3]):
            allowed = ('unspecified', 'unset') if attribute == 'filter' else ('unspecified', 'unset', 'set', 'text', 'binary', 'union')
            if value not in allowed:
                raise web.HTTPBadRequest(text='These changes use Git content filters or merge drivers. Use your Git client instead.')
        entries = checked(repo, 'ls-files', '--stage', '-z', '--', *paths, local=local)
        if any(row.split()[0] not in ('100644', '100755') for row in entries.split('\0') if row):
            raise web.HTTPBadRequest(text='Use your Git client for changes involving links or submodules.')
    for revision in revisions:
        entries = checked(repo, 'ls-tree', '-r', '-z', revision, '--', *paths, local=local) if paths else ''
        if any(row.split()[0] not in ('100644', '100755') for row in entries.split('\0') if row):
            raise web.HTTPBadRequest(text='These changes contain links or submodules. Use your Git client instead.')
        attributes = git(repo, 'grep', '-E', '-e', r'(^|[[:space:]])(filter(=|[[:space:]]|$)|merge=)', revision,
            '--', ':(glob)**/.gitattributes', local=local, environment={'GIT_LITERAL_PATHSPECS': '0'})
        if attributes.returncode not in (0, 1) or not attributes.returncode:
            raise web.HTTPBadRequest(text='These changes use Git content filters or merge drivers. Use your Git client instead.')
    return paths


def stage_all(repo, root, local, index_file=None):
    paths = safe_paths(repo, root, changed_paths(repo, local), local)
    if paths:
        checked(repo, 'add', '--all', '--', *paths, local=local, index_file=index_file)


def discard_all(repo, root, data, body, local):
    paths = [entry['relativePath'] for entry in data['changes']]
    if not paths:
        raise web.HTTPBadRequest(text='There are no unstaged changes to discard')
    # Preflight the entire selection before changing any file.
    safe_paths(repo, root, paths, local)
    with repository_index_lock(repo):
        check_review(repo, data, body, local)
        tracked = [entry['relativePath'] for entry in data['changes'] if entry['status'] != '?']
        untracked = [entry['relativePath'] for entry in data['changes'] if entry['status'] == '?']
        if tracked:
            checked(repo, 'checkout-index', '--force', '--', *tracked, local=local, config=['submodule.recurse=false'])
        for filename in untracked:
            Path(repo, filename).unlink()


def undo(repo, data, body, local):
    reason = menu_state(repo, local)['undoReason']
    if reason:
        raise web.HTTPConflict(text=reason)
    with repository_index_lock(repo):
        check_review(repo, data, body, local)
        head = checked(repo, 'rev-parse', 'HEAD', local=local)
        if body.get('head') != head or body.get('branch') != checked(repo, 'symbolic-ref', '--short', 'HEAD', local=local):
            raise web.HTTPConflict(text='The branch changed. Refresh before undoing its last commit.')
        parents = checked(repo, 'rev-list', '--parents', '-n', '1', head, local=local).split()[1:]
        if parents:
            checked(repo, 'update-ref', '-m', 'llms: undo last commit', 'HEAD', parents[0], head, local=local)
        else:
            checked(repo, 'update-ref', '-d', 'HEAD', head, local=local)
        return {'operation': 'undo', 'message': checked(repo, 'show', '-s', '--format=%B', head, local=local)}


def stash_operation(repo, root, data, action, body, local):
    head = checked(repo, 'rev-parse', '--verify', 'HEAD', local=local)
    if action in ('stash-apply', 'stash-pop'):
        latest = menu_state(repo, local)['stashId']
        if not latest:
            raise web.HTTPBadRequest(text='There is no stash to apply')
        if body.get('stashId') != latest:
            raise web.HTTPConflict(text='The latest stash changed. Refresh and review it before applying.')
        if data['stagedChanges'] or any(entry['status'] != '?' for entry in data['changes']):
            raise web.HTTPConflict(text='Commit or stash your current changes before applying a stash.')
        base = checked(repo, 'rev-parse', latest + '^1', local=local)
        paths = paths_from(repo, 'diff', '--name-only', '--no-renames', base, latest, local=local) + paths_from(repo, 'diff', '--name-only', '--no-renames', base, latest + '^2', local=local)
        extra = git(repo, 'rev-parse', '--verify', latest + '^3', local=local)
        revisions = [head, latest, latest + '^2']
        if not extra.returncode:
            paths += [name for name in checked(repo, 'ls-tree', '-r', '--name-only', '-z', extra.stdout.strip(), local=local, strip=False).split('\0') if name]
            revisions.append(extra.stdout.strip())
        safe_paths(repo, root, list(dict.fromkeys(paths)), local, revisions)
        result = git(repo, 'stash', 'apply', latest, local=local, config=['submodule.recurse=false'],
                     environment={'GIT_LITERAL_PATHSPECS': '0'})
        if result.returncode:
            raise web.HTTPConflict(text='The stash could not be fully applied. It was kept. Refresh and resolve any conflicts with your Git client.')
        if action == 'stash-pop':
            if menu_state(repo, local)['stashId'] != latest:
                raise web.HTTPConflict(text='The stash was applied, but the stash list changed. The stash was kept; review it with your Git client.')
            checked(repo, 'stash', 'drop', 'stash@{0}', local=local)
    else:
        paths = changed_paths(repo, local)
        safe_paths(repo, root, paths, local, [head])
        if action == 'stash-staged' and not data['stagedChanges']:
            raise web.HTTPBadRequest(text='Stage changes before stashing staged files')
        if not data['stagedChanges'] and not any(entry['status'] != '?' or action == 'stash-untracked' for entry in data['changes']):
            raise web.HTTPBadRequest(text='There are no changes for this stash operation')
        identity = body.get('identity') or data['identity']
        if not isinstance(identity, dict) or any(not isinstance(identity.get(key), str) or not identity[key].strip()
                or len(identity[key]) > 256 or any(c in identity[key] for c in '\n\r\0<>') for key in ('name', 'email')):
            raise web.HTTPBadRequest(text='Enter your Git author name and email before stashing')
        options = ['--include-untracked'] if action == 'stash-untracked' else ['--staged'] if action == 'stash-staged' else []
        # Stash uses generated pathspecs internally (including cleanup of nested untracked files).
        # No caller paths are passed, so allow Git's internal pathspec handling here.
        result = git(repo, 'stash', 'push', *options, local=local, identity=identity, config=['submodule.recurse=false'],
                     environment={'GIT_LITERAL_PATHSPECS': '0'})
        if result.returncode:
            raise web.HTTPConflict(text='Git could not finish stashing these changes. Refresh and review any saved stash with your Git client. Stash Staged requires Git 2.35 or newer.')
    return {'operation': action}


def repository_operation(repo, root, data, action, body, local):
    check_review(repo, data, body, local)
    if action == 'stage-all':
        stage_all(repo, root, local)
    elif action == 'unstage-all':
        paths = validate_paths(repo, root, paths_from(repo, 'diff', '--cached', '--name-only', '--no-renames', local=local))
        if git(repo, 'rev-parse', '--verify', 'HEAD', local=local).returncode:
            checked(repo, 'rm', '--cached', '-f', '--', *paths, local=local)
        else:
            checked(repo, 'restore', '--staged', '--', *paths, local=local)
    elif action == 'discard-all':
        discard_all(repo, root, data, body, local)
    elif action == 'undo':
        return undo(repo, data, body, local)
    else:
        return stash_operation(repo, root, data, action, body, local)
    return {'operation': action}
