"""Explicit remote sync using the selected branch, without force pushes or merges."""
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from aiohttp import web
from .operations import active_run, checked, git, validate_repo
from .provision import GitProvisioner
from llms.workspace_operations import operation_lock, workspace_submission_lock


def config_values(repo, key, local=True):
    result = git(repo, 'config', '--get-all', key, local=local)
    return result.stdout.strip().splitlines() if not result.returncode else []


def github_repository_url(url):
    scp = re.fullmatch(r'git@github\.com:([^\s]+)', url)
    if scp:
        path = scp[1]
    else:
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in ('https', 'ssh') or parsed.hostname != 'github.com' or parsed.password
                    or parsed.query or parsed.fragment or parsed.port not in (None, 22, 443)
                    or parsed.username not in (None, 'git')):
                return None
            path = parsed.path.lstrip('/')
        except ValueError:
            return None
    path = path.rstrip('/')
    if path.endswith('.git'):
        path = path[:-4]
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', path) or any(part in ('.', '..') for part in path.split('/')):
        return None
    return 'https://github.com/' + path


def sync_state(repo, local=True):
    branch = git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD', local=local)
    branch = branch.stdout.strip() if not branch.returncode else None
    head = git(repo, 'rev-parse', '--verify', 'HEAD', local=local)
    head = head.stdout.strip() if not head.returncode else ''
    upstream = config_values(repo, 'branch.' + branch + '.remote', local) if branch else []
    merge = config_values(repo, 'branch.' + branch + '.merge', local) if branch else []
    remotes = []
    for name in checked(repo, 'remote', local=local).splitlines():
        if not re.fullmatch(r'[\w.-]+', name) or name.startswith('-'):
            continue
        target = merge[0][len('refs/heads/'):] if upstream == [name] and len(merge) == 1 and merge[0].startswith('refs/heads/') else branch
        ahead = behind = None
        if head and target and not git(repo, 'check-ref-format', 'refs/heads/' + target, local=local).returncode:
            count = git(repo, 'rev-list', '--left-right', '--count', 'HEAD...refs/remotes/' + name + '/' + target, '--', local=local)
            if not count.returncode:
                ahead, behind = map(int, count.stdout.split())
        urls = config_values(repo, 'remote.' + name + '.url', local)
        remotes.append({'name': name, 'branch': target, 'ahead': ahead, 'behind': behind,
                        'githubUrl': github_repository_url(urls[0]) if len(urls) == 1 else None})
    selected = next((entry['name'] for entry in remotes if upstream == [entry['name']]), None)
    selected = selected or next((entry['name'] for entry in remotes if entry['name'] == 'origin'), None)
    return {'head': head, 'remotes': remotes, 'remote': selected or (remotes[0]['name'] if remotes else None),
            'canSync': bool(branch and head and os.path.isdir(os.path.join(repo, '.git'))),
            'canPush': bool(local)}


def remote_url(repo, name, action, local, hosts):
    urls = config_values(repo, 'remote.' + name + '.pushurl', local) if action == 'push' else []
    urls = urls or config_values(repo, 'remote.' + name + '.url', local)
    if len(urls) != 1:
        raise web.HTTPBadRequest(text='Configure one repository URL for this remote with your Git client.')
    url = urls[0]
    # URL rewriting can silently change the reviewed transport or destination.
    rewrite = git(repo, 'config', '--get-regexp', r'^url\..*\.(insteadof|pushinsteadof)$', local=local)
    if not rewrite.returncode:
        raise web.HTTPBadRequest(text='This repository uses Git URL rewriting. Sync it with your Git client instead.')
    if local and os.path.isabs(url) and os.path.isdir(url):
        return url, 'file'
    source = GitProvisioner('git', local=local, hosts=hosts).validate({'url': url})
    return source['url'], 'https' if source['url'].startswith('https://') else 'ssh'


def transport(repo, action, url, protocol, target, local, *, allow_missing_branch=False):
    configuration = ['protocol.allow=never', 'protocol.' + protocol + '.allow=always',
                     'credential.interactive=false', 'fetch.recurseSubmodules=false',
                     'submodule.recurse=false', 'http.followRedirects=false']
    if not local:
        configuration += ['credential.helper=', 'http.extraHeader=', 'http.cookieFile=']
        # URL-specific headers/credentials also belong to the server operator, not this user.
        private = git(repo, 'config', '--get-regexp', r'^(http\..*\.(extraheader|cookiefile)|credential\..*\.helper)$', local=False)
        if not private.returncode:
            raise web.HTTPForbidden(text='Repository-specific HTTP credentials are unavailable in hosted mode.')
    environment = {'GIT_ASKPASS': '', 'SSH_ASKPASS': '', 'GIT_LFS_SKIP_SMUDGE': '1',
                   'GIT_SSH_COMMAND': 'ssh -oBatchMode=yes -oStrictHostKeyChecking=yes'}
    if action == 'push':
        args = ['push', '--porcelain', '--no-verify', '--no-follow-tags', '--recurse-submodules=no',
                '--receive-pack=git-receive-pack', '--', url, target]
    else:
        args = ['fetch', '--no-tags', '--no-recurse-submodules', '--upload-pack=git-upload-pack', '--', url, target]
    result = git(repo, *args, local=local, config=configuration, environment=environment, timeout=120)
    if result.returncode:
        error = result.stderr.lower()
        if action == 'pull' and allow_missing_branch and "couldn't find remote ref" in error:
            return False
        if any(text in error for text in ('authentication failed', 'permission denied', 'could not read username', 'publickey')):
            message = 'Git authentication failed. Configure your local Git credentials or SSH key, then retry.'
        elif any(text in error + result.stdout.lower() for text in ('non-fast-forward', '[rejected]', 'fetch first')):
            message = 'The remote has changes. Pull before pushing; diverged branches need resolving with your Git client.'
        else:
            message = 'Unable to ' + action + '. Check the remote URL, access permissions, and connection, then refresh.'
        raise web.HTTPConflict(text=message)
    return True


def sync_repository(roots, path, action, body, *, data_root, user=None, project_id=None, db=None, local=True, hosts=None):
    if action not in ('push', 'pull', 'sync'):
        raise web.HTTPBadRequest(text='Unknown sync operation')
    if action in ('push', 'sync') and not local:
        raise web.HTTPForbidden(text='Push is available in local mode using your Git credentials. Hosted push requires per-user Git authentication.')
    with workspace_submission_lock(data_root):
        _, _, _, repo = validate_repo(roots, path)
        with operation_lock(os.path.join(repo, '.git', 'llms-locks'), 'repository'):
            if active_run(db, user, project_id, repo):
                raise web.HTTPConflict(text='An agent is active in this workspace. Wait for it to finish before syncing.')
            state = sync_state(repo, local)
            if not state['canSync']:
                raise web.HTTPBadRequest(text='Create a commit and check out a branch before syncing.')
            if body.get('head') != state['head'] or body.get('branch') != checked(repo, 'symbolic-ref', '--short', 'HEAD', local=local):
                raise web.HTTPConflict(text='The branch changed. Refresh and review it before syncing.')
            remote = next((entry for entry in state['remotes'] if entry['name'] == body.get('remote')), None)
            if not remote or not remote['branch'] or git(repo, 'check-ref-format', 'refs/heads/' + remote['branch'], local=local).returncode:
                raise web.HTTPBadRequest(text='Select a configured Git remote.')
            if body.get('remoteBranch') != remote['branch']:
                raise web.HTTPConflict(text='The remote branch changed. Refresh before syncing.')
            url, protocol = remote_url(repo, remote['name'], 'pull' if action == 'sync' else action, local, hosts)
            push_url, push_protocol = remote_url(repo, remote['name'], 'push', local, hosts) if action == 'sync' else (url, protocol)
            tracking = 'refs/remotes/' + remote['name'] + '/' + remote['branch']
            metadata = Path(repo, '.git')
            for relative in (tracking, 'logs/' + tracking):
                target = metadata / relative
                while target != metadata:
                    if target.is_symlink():
                        raise web.HTTPForbidden(text='Linked Git remote references are not supported.')
                    target = target.parent
            if action == 'push':
                transport(repo, action, url, protocol, state['head'] + ':refs/heads/' + remote['branch'], local)
                checked(repo, 'update-ref', tracking, state['head'], local=local)
            else:
                if checked(repo, 'status', '--porcelain=v1', '--untracked-files=all', local=local):
                    raise web.HTTPConflict(text='Commit or stash your working changes before pulling.')
                # Pull never starts a merge, rebases, auto-stashes, or invokes a checkout filter.
                fetched = transport(repo, 'pull', url, protocol, '+refs/heads/' + remote['branch'] + ':' + tracking, local,
                                    allow_missing_branch=action == 'sync' and remote['ahead'] is None)
                revision = state['head'] if fetched is False else checked(repo, 'rev-parse', '--verify', tracking + '^{commit}', local=local)
                if (git(repo, 'merge-base', '--is-ancestor', state['head'], revision, local=local).returncode
                        and git(repo, 'merge-base', '--is-ancestor', revision, state['head'], local=local).returncode):
                    raise web.HTTPConflict(text='The branches have diverged. Resolve them with your Git client; your local commits were kept.')
                # Recheck after network I/O so external edits are never overwritten.
                if (checked(repo, 'rev-parse', 'HEAD', local=local) != state['head']
                        or checked(repo, 'symbolic-ref', '--short', 'HEAD', local=local) != body['branch']
                        or checked(repo, 'status', '--porcelain=v1', '--untracked-files=all', local=local)):
                    raise web.HTTPConflict(text='The repository changed while fetching. Refresh before pulling again.')
                paths = checked(repo, 'diff', '--name-only', '-z', state['head'], revision, '--', local=local).split('\0')
                paths = [name for name in paths if name]
                if len(paths) > 10000:
                    raise web.HTTPBadRequest(text='This update is too large. Pull with your Git client instead.')
                attributes = checked(repo, 'check-attr', '-z', 'filter', '--', *paths, local=local).split('\0') if paths else []
                incoming = git(repo, 'grep', '-E', '-e', r'(^|[[:space:]])filter(=|[[:space:]]|$)', revision,
                               '--', ':(glob)**/.gitattributes', local=local, environment={'GIT_LITERAL_PATHSPECS': '0'})
                if incoming.returncode not in (0, 1):
                    raise web.HTTPBadRequest(text='Unable to check incoming Git attributes. Pull with your Git client instead.')
                if (any(value not in ('unspecified', 'unset') for value in attributes[2::3]) or not incoming.returncode):
                    raise web.HTTPBadRequest(text='This update uses Git content filters. Pull with your Git client instead.')
                head = state['head'] if not git(repo, 'merge-base', '--is-ancestor', revision, state['head'], local=local).returncode else revision
                checked(repo, 'merge', '--ff-only', '--no-edit', revision, local=local,
                        config=['submodule.recurse=false'])
                if action == 'sync':
                    if (checked(repo, 'rev-parse', 'HEAD', local=local) != head
                            or checked(repo, 'symbolic-ref', '--short', 'HEAD', local=local) != body['branch']
                            or checked(repo, 'status', '--porcelain=v1', '--untracked-files=all', local=local)):
                        raise web.HTTPConflict(text='The repository changed while syncing. Refresh before syncing again.')
                    transport(repo, 'push', push_url, push_protocol, head + ':refs/heads/' + remote['branch'], local)
                    checked(repo, 'update-ref', tracking, head, local=local)
            # Remember the first sync destination without replacing an existing upstream.
            branch = body['branch']
            if not config_values(repo, 'branch.' + branch + '.remote', local):
                checked(repo, 'config', '--local', 'branch.' + branch + '.remote', remote['name'], local=local)
                checked(repo, 'config', '--local', 'branch.' + branch + '.merge', 'refs/heads/' + remote['branch'], local=local)
            return {'operation': action, 'remote': remote['name'], 'branch': remote['branch']}
