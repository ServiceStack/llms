import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aiohttp import web
from llms.extensions.git import browse_git, install
from llms.extensions.git.operations import git
from llms.extensions.git.sync import github_repository_url, remote_url, sync_repository, sync_state, transport
from llms.workspace_operations import workspace_submission_lock


@unittest.skipUnless(shutil.which('git'), 'Git unavailable')
class GitSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = str(Path(self.directory.name, 'repo'))
        self.bare = str(Path(self.directory.name, 'remote.git'))
        self.other = str(Path(self.directory.name, 'other'))
        Path(self.repo).mkdir()
        self.command(self.repo, 'init', '-q', '-b', 'main')
        self.command(self.repo, 'config', 'user.name', 'Test')
        self.command(self.repo, 'config', 'user.email', 'test@example.com')
        Path(self.repo, 'one.txt').write_text('Original\n')
        self.commit(self.repo, 'Initial commit')
        self.command(self.repo, 'init', '--bare', '-q', '--initial-branch=main', self.bare)
        self.command(self.repo, 'remote', 'add', 'origin', self.bare)

    def tearDown(self):
        self.directory.cleanup()

    def command(self, repo, *args):
        result = git(repo, *args, local=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, repo, message):
        self.command(repo, 'add', '--all')
        self.command(repo, 'commit', '-q', '-m', message)

    def sync(self, action, body=None, **kwargs):
        state = sync_state(self.repo)
        destination = next(entry for entry in state['remotes'] if entry['name'] == (body or {}).get('remote', state['remote']))
        request = {'head': state['head'], 'branch': 'main', 'remote': destination['name'], 'remoteBranch': destination['branch']}
        return sync_repository([self.repo], self.repo, action, {**request, **(body or {})},
                               data_root=self.directory.name, user='alice', **kwargs)

    def clone_other(self):
        self.sync('push')
        self.command(self.repo, 'clone', '-q', '--', self.bare, self.other)
        self.command(self.other, 'config', 'user.name', 'Other')
        self.command(self.other, 'config', 'user.email', 'other@example.com')

    def remote_commit(self):
        Path(self.other, 'one.txt').write_text('Remote change\n')
        self.commit(self.other, 'Remote change')
        self.command(self.other, 'push', '-q', 'origin', 'main')

    def test_first_push_sets_upstream_and_refreshes_counts(self):
        self.assertIsNone(sync_state(self.repo)['remotes'][0]['ahead'])
        result = self.sync('push')
        self.assertEqual(result, {'operation': 'push', 'remote': 'origin', 'branch': 'main'})
        self.assertEqual(self.command(self.repo, 'rev-parse', '@{upstream}'), self.command(self.repo, 'rev-parse', 'HEAD'))
        self.assertEqual(self.command(self.bare, 'rev-parse', 'main'), self.command(self.repo, 'rev-parse', 'HEAD'))
        Path(self.repo, 'two.txt').write_text('Local addition\n')
        self.commit(self.repo, 'Local addition')
        data = browse_git([self.repo])
        self.assertEqual(data['remotes'][0]['ahead'], 1)
        self.assertTrue(data['canPush'])
        self.sync('push')
        self.assertEqual(sync_state(self.repo)['remotes'][0]['ahead'], 0)

    def test_pull_fast_forward_and_retry(self):
        self.clone_other()
        self.remote_commit()
        self.sync('pull')
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Remote change\n')
        self.assertEqual(self.command(self.repo, 'log', '-1', '--format=%s'), 'Remote change')
        self.sync('pull')
        self.assertEqual(self.command(self.repo, 'rev-list', '--count', 'HEAD'), '2')

    def test_sync_publishes_a_new_branch_and_pushes_local_commits(self):
        result = self.sync('sync')
        self.assertEqual(result, {'operation': 'sync', 'remote': 'origin', 'branch': 'main'})
        self.assertEqual(self.command(self.repo, 'rev-parse', '@{upstream}'), self.command(self.repo, 'rev-parse', 'HEAD'))
        Path(self.repo, 'two.txt').write_text('Local addition\n')
        self.commit(self.repo, 'Local addition')
        self.assertEqual(sync_state(self.repo)['remotes'][0]['ahead'], 1)
        self.sync('sync')
        self.assertEqual(self.command(self.bare, 'rev-parse', 'main'), self.command(self.repo, 'rev-parse', 'HEAD'))
        self.assertEqual(sync_state(self.repo)['remotes'][0]['ahead'], 0)
        self.sync('sync')
        self.assertEqual(self.command(self.repo, 'rev-list', '--count', 'HEAD'), '2')

    def test_sync_fetches_new_remote_updates_before_pushing(self):
        self.clone_other()
        self.remote_commit()
        self.assertEqual(sync_state(self.repo)['remotes'][0]['behind'], 0)
        from llms.extensions.git.sync import transport as real_transport
        with patch('llms.extensions.git.sync.transport', wraps=real_transport) as network:
            self.sync('sync')
        self.assertEqual([call.args[1] for call in network.call_args_list], ['pull', 'push'])
        self.assertEqual(network.call_args_list[1].args[4].split(':')[0], self.command(self.other, 'rev-parse', 'HEAD'))
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Remote change\n')
        self.assertEqual(sync_state(self.repo)['remotes'][0]['behind'], 0)

    def test_sync_stops_before_push_on_divergence_or_dirty_worktree(self):
        self.clone_other()
        self.remote_commit()
        Path(self.repo, 'two.txt').write_text('Local\n')
        self.commit(self.repo, 'Local commit')
        before = self.command(self.repo, 'rev-parse', 'HEAD')
        remote_before = self.command(self.bare, 'rev-parse', 'main')
        from llms.extensions.git.sync import transport as real_transport
        with patch('llms.extensions.git.sync.transport', wraps=real_transport) as network:
            with self.assertRaises(web.HTTPConflict) as error:
                self.sync('sync')
            self.assertIn('diverged', error.exception.text)
            self.assertEqual([call.args[1] for call in network.call_args_list], ['pull'])
        self.assertEqual(self.command(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertEqual(self.command(self.bare, 'rev-parse', 'main'), remote_before)
        self.assertFalse(Path(self.repo, '.git/MERGE_HEAD').exists())
        Path(self.repo, 'two.txt').write_text('Keep edits\n')
        with patch('llms.extensions.git.sync.transport') as network:
            with self.assertRaises(web.HTTPConflict):
                self.sync('sync')
            network.assert_not_called()
        self.assertEqual(Path(self.repo, 'two.txt').read_text(), 'Keep edits\n')

    def test_sync_failed_push_keeps_pulled_updates_and_retry_is_safe(self):
        self.clone_other()
        self.remote_commit()
        from llms.extensions.git.sync import transport as real_transport
        def fail_push(repo, action, *args, **kwargs):
            if action == 'push':
                raise web.HTTPConflict(text='Git authentication failed')
            return real_transport(repo, action, *args, **kwargs)
        with patch('llms.extensions.git.sync.transport', side_effect=fail_push):
            with self.assertRaises(web.HTTPConflict):
                self.sync('sync')
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Remote change\n')
        self.sync('sync')
        self.assertEqual(self.command(self.repo, 'rev-parse', 'HEAD'), self.command(self.bare, 'rev-parse', 'main'))

    def test_sync_stops_if_branch_changes_to_the_same_head_during_fetch(self):
        self.clone_other()
        self.remote_commit()
        before = self.command(self.repo, 'rev-parse', 'HEAD')
        from llms.extensions.git.sync import transport as real_transport
        def switch_after_fetch(*args, **kwargs):
            result = real_transport(*args, **kwargs)
            self.command(self.repo, 'checkout', '-q', '-b', 'other-branch')
            return result
        with patch('llms.extensions.git.sync.transport', side_effect=switch_after_fetch) as network:
            with self.assertRaises(web.HTTPConflict):
                self.sync('sync')
            self.assertEqual(network.call_count, 1)
        self.assertEqual(self.command(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Original\n')

    def test_pull_when_local_branch_is_ahead_keeps_commits(self):
        self.sync('push')
        Path(self.repo, 'two.txt').write_text('Local\n')
        self.commit(self.repo, 'Local commit')
        before = self.command(self.repo, 'rev-parse', 'HEAD')
        self.sync('pull')
        self.assertEqual(self.command(self.repo, 'rev-parse', 'HEAD'), before)

    def test_dirty_and_staged_and_untracked_pull_are_rejected(self):
        self.clone_other()
        self.remote_commit()
        Path(self.repo, 'one.txt').write_text('Keep my edits\n')
        for staged in (False, True):
            if staged:
                self.command(self.repo, 'add', '--', 'one.txt')
            with self.assertRaises(web.HTTPConflict):
                self.sync('pull')
            self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Keep my edits\n')
        self.commit(self.repo, 'Keep edits')
        Path(self.repo, 'untracked.txt').write_text('Keep me')
        with self.assertRaises(web.HTTPConflict):
            self.sync('pull')
        self.assertEqual(Path(self.repo, 'untracked.txt').read_text(), 'Keep me')

    def test_divergence_and_push_rejection_preserve_local_commits(self):
        self.clone_other()
        self.remote_commit()
        Path(self.repo, 'two.txt').write_text('Local\n')
        self.commit(self.repo, 'Local commit')
        before = self.command(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaises(web.HTTPConflict):
            self.sync('push')
        with self.assertRaises(web.HTTPConflict):
            self.sync('pull')
        self.assertEqual(self.command(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertFalse(Path(self.repo, '.git/MERGE_HEAD').exists())
        self.assertEqual(self.command(self.bare, 'log', '-1', '--format=%s'), 'Remote change')
        self.assertEqual(sync_state(self.repo)['remotes'][0]['behind'], 1)

    def test_stale_head_branch_remote_and_agent_gate(self):
        for body in ({'head': '0' * 40}, {'branch': 'other'}, {'remoteBranch': 'other'}):
            with self.assertRaises(web.HTTPConflict):
                self.sync('push', body)
        with workspace_submission_lock(self.directory.name):
            with self.assertRaises(web.HTTPConflict):
                self.sync('push')
        with patch('llms.extensions.git.sync.active_run', return_value=True):
            with self.assertRaises(web.HTTPConflict):
                self.sync('push')
        self.command(self.repo, 'checkout', '--detach', '-q')
        with self.assertRaises(web.HTTPBadRequest):
            self.sync('push')

    def test_remote_policy_and_hosted_mode(self):
        with self.assertRaises(web.HTTPForbidden):
            self.sync('push', local=False)
        with self.assertRaises(web.HTTPForbidden):
            self.sync('sync', local=False)
        self.command(self.repo, 'remote', 'set-url', 'origin', 'ext::sh -c bad')
        with self.assertRaises(web.HTTPBadRequest):
            self.sync('push')
        self.command(self.repo, 'remote', 'set-url', 'origin', 'https://secret@github.com/user/repo.git')
        with self.assertRaises(web.HTTPBadRequest):
            self.sync('pull')
        self.command(self.repo, 'remote', 'set-url', 'origin', 'https://example.com/user/repo.git')
        with self.assertRaises(web.HTTPBadRequest):
            self.sync('pull', local=False)
        self.command(self.repo, 'remote', 'set-url', 'origin', 'git@github.com:user/repo.git')
        self.assertEqual(remote_url(self.repo, 'origin', 'push', True, None)[1], 'ssh')
        with self.assertRaises(web.HTTPBadRequest):
            remote_url(self.repo, 'origin', 'pull', False, None)
        self.command(self.repo, 'config', 'url.ext::unsafe.insteadOf', 'git@github.com:')
        with self.assertRaises(web.HTTPBadRequest):
            self.sync('push')

    def test_filters_hooks_and_linked_refs(self):
        self.clone_other()
        self.remote_commit()
        marker = Path(self.directory.name, 'hook-ran')
        hook = Path(self.repo, '.git/hooks/post-merge')
        hook.write_text('#!/bin/sh\ntouch ' + str(marker) + '\n')
        hook.chmod(0o755)
        Path(self.repo, '.git/info/attributes').write_text('*.txt filter=unsafe\n')
        self.command(self.repo, 'config', 'filter.unsafe.smudge', 'touch ' + str(marker))
        with self.assertRaises(web.HTTPBadRequest):
            self.sync('pull')
        self.assertFalse(marker.exists())
        self.command(self.repo, 'config', '--unset', 'filter.unsafe.smudge')
        Path(self.repo, '.git/info/attributes').unlink()
        self.sync('pull')
        self.assertFalse(marker.exists())
        if os.name != 'nt':
            reference = Path(self.repo, '.git/refs/remotes/origin/main')
            reference.unlink()
            reference.symlink_to(Path(self.other, '.git/refs/heads/main'))
            with self.assertRaises(web.HTTPForbidden):
                self.sync('push')

    def test_different_upstream_branch_and_selected_remote(self):
        self.command(self.repo, 'config', 'branch.main.remote', 'origin')
        self.command(self.repo, 'config', 'branch.main.merge', 'refs/heads/release')
        self.assertEqual(sync_state(self.repo)['remotes'][0]['branch'], 'release')
        self.sync('push')
        self.assertEqual(self.command(self.bare, 'rev-parse', 'release'), self.command(self.repo, 'rev-parse', 'HEAD'))
        self.command(self.repo, 'remote', 'add', 'backup', self.bare)
        self.sync('push', {'remote': 'backup'})
        self.assertEqual(self.command(self.repo, 'config', 'branch.main.remote'), 'origin')
        self.assertEqual(self.command(self.bare, 'rev-parse', 'main'), self.command(self.repo, 'rev-parse', 'HEAD'))

    def test_external_edit_during_fetch_is_kept(self):
        self.clone_other()
        self.remote_commit()
        from llms.extensions.git.sync import transport
        def edit_after_fetch(*args, **kwargs):
            transport(*args, **kwargs)
            Path(self.repo, 'one.txt').write_text('Concurrent edit\n')
        with patch('llms.extensions.git.sync.transport', side_effect=edit_after_fetch):
            with self.assertRaises(web.HTTPConflict):
                self.sync('pull')
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Concurrent edit\n')

    def test_new_incoming_nested_attributes_cannot_run_a_filter(self):
        self.clone_other()
        Path(self.other, 'nested').mkdir()
        Path(self.other, 'nested/.gitattributes').write_text('*.txt filter=unsafe\n')
        Path(self.other, 'nested/new.txt').write_text('Remote file\n')
        self.commit(self.other, 'Introduce filtered file')
        self.command(self.other, 'push', '-q', 'origin', 'main')
        marker = Path(self.directory.name, 'filter-ran')
        self.command(self.repo, 'config', 'filter.unsafe.smudge', 'touch ' + str(marker))
        with self.assertRaises(web.HTTPBadRequest) as error:
            self.sync('pull')
        self.assertIn('content filters', error.exception.text)
        self.assertFalse(marker.exists())
        self.assertFalse(Path(self.repo, 'nested/new.txt').exists())

    def test_push_does_not_implicitly_follow_tags(self):
        self.command(self.repo, 'config', 'push.followTags', 'true')
        self.command(self.repo, 'tag', '-a', 'v1', '-m', 'Release')
        self.sync('push')
        self.assertFalse(self.command(self.bare, 'tag'))

    def test_hosted_pull_does_not_use_repository_scoped_credentials(self):
        self.command(self.repo, 'config', 'credential.https://github.com.helper', 'unsafe-helper')
        with self.assertRaises(web.HTTPForbidden):
            transport(self.repo, 'pull', 'https://github.com/user/repo.git', 'https', 'refs/heads/main', False)

    def test_transport_uses_noninteractive_credentials_and_sanitized_errors(self):
        result = SimpleNamespace(returncode=1, stdout='', stderr='Authentication failed: https://secret-token@github.com/user/repo')
        with patch('llms.extensions.git.sync.git', return_value=result) as command:
            with self.assertRaises(web.HTTPConflict) as error:
                transport(self.repo, 'push', 'git@github.com:user/repo.git', 'ssh', 'HEAD:refs/heads/main', True)
            self.assertNotIn('secret-token', error.exception.text)
            self.assertIn('authentication failed', error.exception.text)
            arguments = command.call_args
            self.assertIn('protocol.allow=never', arguments.kwargs['config'])
            self.assertIn('StrictHostKeyChecking=yes', arguments.kwargs['environment']['GIT_SSH_COMMAND'])
            self.assertEqual(arguments.kwargs['timeout'], 120)


class GitSyncRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_combined_sync_route_is_registered(self):
        ctx = Mock()
        ctx.config = {}
        install(ctx)
        self.assertIn('repositories/{action:push|pull|sync}', [call.args[0] for call in ctx.add_post.call_args_list])

    async def test_authentication_and_explicit_workspace_resolution(self):
        ctx = Mock()
        ctx.is_local = True
        ctx.config = {}
        ctx.app = SimpleNamespace(agent_db=None)
        ctx.is_admin.return_value = False
        ctx.projects.resolve_explorer_workspace.return_value = {'directories': ['/allowed']}
        ctx.check_auth.return_value = (True, None)
        ctx.get_username.return_value = 'alice'
        ctx.get_user_path.return_value = '/data'
        install(ctx)
        handler = next(call.args[1] for call in ctx.add_post.call_args_list if 'push|pull' in call.args[0])
        request = Mock()
        request.match_info = {'action': 'push'}
        from unittest.mock import AsyncMock
        request.json = AsyncMock(return_value={'projectId': 'selected-project', 'path': '/allowed'})
        with patch('llms.extensions.git.sync.sync_repository', return_value={'operation': 'push'}) as sync:
            response = await handler(request)
            self.assertEqual(json.loads(response.text)['operation'], 'push')
            ctx.projects.resolve_explorer_workspace.assert_called_once_with('selected-project', 'alice', is_admin=False)
            self.assertEqual(sync.call_args.kwargs['project_id'], 'selected-project')
            ctx.check_auth.return_value = (False, None)
            ctx.error_auth_required = {'error': 'Authentication required'}
            self.assertEqual((await handler(request)).status, 401)
            self.assertEqual(sync.call_count, 1)


class GitHubUrlTests(unittest.TestCase):
    def test_https_and_ssh_remotes_have_commit_browser_urls(self):
        for url in ('https://github.com/ServiceStack/llms.git', 'git@github.com:ServiceStack/llms.git',
                    'ssh://git@github.com/ServiceStack/llms', 'https://github.com/ServiceStack/llms/'):
            self.assertEqual(github_repository_url(url), 'https://github.com/ServiceStack/llms')

    def test_non_github_and_unsafe_urls_are_unavailable(self):
        for url in ('https://gitlab.com/team/repo', 'https://github.com.evil.test/team/repo',
                    'https://secret@github.com/team/repo', 'https://github.com/team/repo?token=secret',
                    'https://github.com/team/repo/pulls', 'https://github.com/team/..',
                    'javascript:alert(1)', '/tmp/remote.git'):
            self.assertIsNone(github_repository_url(url))
