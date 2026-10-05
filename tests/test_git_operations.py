import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from aiohttp import web
from llms.extensions.git import browse_git, diff_git, install
from llms.extensions.git.operations import git, mutate, repository_state
from llms.workspace_operations import workspace_submission_lock


@unittest.skipUnless(shutil.which('git'), 'Git unavailable')
class GitOperationsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = str(Path(self.directory.name, 'repo'))
        Path(self.repo).mkdir()
        self.git('init', '-q')
        self.file('one.txt', 'First\n')
        self.file('two.txt', 'Other\n')

    def tearDown(self):
        self.directory.cleanup()

    def git(self, *args):
        result = git(self.repo, *args, local=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def file(self, name, content):
        path = Path(self.repo, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def mutate(self, action, body, **kwargs):
        return mutate([self.repo], self.repo, action, body, data_root=self.directory.name,
                      user='alice', local=False, **kwargs)

    def stage(self, *paths):
        return self.mutate('stage', {'paths': list(paths)})

    def discard_body(self, filename):
        data = browse_git([self.repo])
        entry = next(item for item in data['changes'] if item['relativePath'] == filename)
        return {'paths': [filename], 'indexRevision': data['indexRevision'], 'worktreeRevision': entry['worktreeRevision']}

    def test_discard_restores_index_and_keeps_staged_changes(self):
        self.stage('one.txt')
        self.file('one.txt', 'Unstaged\n')
        before = repository_state(self.repo)['indexRevision']
        self.mutate('discard', self.discard_body('one.txt'))
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'First\n')
        self.assertEqual(self.git('show', ':one.txt'), 'First')
        self.assertEqual(repository_state(self.repo)['indexRevision'], before)
        self.mutate('commit', self.commit_body())
        self.file('one.txt', 'Staged version\n')
        self.stage('one.txt')
        self.file('one.txt', 'Later edits\n')
        self.mutate('discard', self.discard_body('one.txt'))
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Staged version\n')
        self.assertEqual(self.git('show', 'HEAD:one.txt'), 'First')
        self.assertEqual(len(browse_git([self.repo])['stagedChanges']), 1)

    def test_discard_restores_deleted_tracked_file_and_deletes_only_selected_untracked_file(self):
        self.stage('one.txt')
        self.mutate('commit', self.commit_body())
        Path(self.repo, 'one.txt').unlink()
        self.mutate('discard', self.discard_body('one.txt'))
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'First\n')
        self.file('nested/literal[1].txt', 'Remove me')
        self.file('nested/keep.txt', 'Keep me')
        self.mutate('discard', self.discard_body('nested/literal[1].txt'))
        self.assertFalse(Path(self.repo, 'nested/literal[1].txt').exists())
        self.assertEqual(Path(self.repo, 'nested/keep.txt').read_text(), 'Keep me')
        self.assertEqual(Path(self.repo, 'two.txt').read_text(), 'Other\n')

    def test_discard_rejects_stale_worktree_index_and_active_runs(self):
        body = self.discard_body('one.txt')
        self.file('one.txt', 'New edits')
        with self.assertRaises(web.HTTPConflict):
            self.mutate('discard', body)
        body = self.discard_body('one.txt')
        self.stage('two.txt')
        with self.assertRaises(web.HTTPConflict):
            self.mutate('discard', body)
        with patch('llms.extensions.git.operations.active_run', return_value=True):
            with self.assertRaises(web.HTTPConflict):
                self.mutate('discard', self.discard_body('one.txt'))
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'New edits')

    def test_discard_rejects_filters_linked_paths_and_directory_targets(self):
        self.stage('one.txt')
        self.file('one.txt', 'Keep edits')
        self.file('.gitattributes', '*.txt filter=unsafe\n')
        marker = Path(self.directory.name, 'filter-ran')
        self.git('config', 'filter.unsafe.smudge', 'touch ' + str(marker))
        with self.assertRaises(web.HTTPBadRequest):
            self.mutate('discard', self.discard_body('one.txt'))
        self.assertFalse(marker.exists())
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Keep edits')
        for path in ('../outside', '.git/config', '/etc/passwd', 'nested'):
            with self.assertRaises((web.HTTPBadRequest, web.HTTPForbidden, web.HTTPConflict)):
                self.mutate('discard', {'paths': [path]})
        if os.name != 'nt':
            Path(self.repo, 'link.txt').symlink_to(Path(self.repo, 'two.txt'))
            with self.assertRaises(web.HTTPBadRequest):
                self.mutate('discard', self.discard_body('link.txt'))
            self.assertEqual(Path(self.repo, 'two.txt').read_text(), 'Other\n')

    def commit_body(self, message='Save selected files'):
        return {'message': message, 'indexRevision': repository_state(self.repo, False)['indexRevision'],
                'identity': {'name': 'Test Author', 'email': 'test@example.com'}, 'requestId': str(uuid.uuid4())}

    def test_history_keeps_full_message_for_copying(self):
        self.stage('one.txt')
        message = 'Subject\ncontinued subject\n\nDetails and rationale.\n'
        result = self.mutate('commit', self.commit_body(message))
        entry = browse_git([self.repo])['commits'][0]
        self.assertEqual(entry['message'], message)
        self.assertEqual(entry['id'], result['revision'])

    def repository_body(self):
        data = browse_git([self.repo], local=False)
        return {key: data.get(key) for key in ('head', 'branch', 'indexRevision', 'workspaceRevision', 'stashId')}

    def initialize_history(self):
        self.stage('one.txt', 'two.txt')
        self.mutate('commit', self.commit_body('Initial commit'))

    def test_stage_and_unstage_all_preserve_worktree_in_unborn_and_existing_repo(self):
        self.mutate('stage-all', self.repository_body())
        self.assertEqual(len(browse_git([self.repo])['stagedChanges']), 2)
        self.mutate('unstage-all', self.repository_body())
        self.assertFalse(browse_git([self.repo])['stagedChanges'])
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'First\n')
        self.initialize_history()
        self.file('one.txt', 'New\n')
        self.file('literal[1].txt', 'Added\n')
        self.mutate('stage-all', self.repository_body())
        self.assertEqual(len(browse_git([self.repo])['stagedChanges']), 2)
        self.mutate('unstage-all', self.repository_body())
        self.assertFalse(browse_git([self.repo])['stagedChanges'])
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'New\n')

    def test_commit_all_includes_untracked_files_and_retry_keeps_one_commit(self):
        self.stage('one.txt')
        self.file('one.txt', 'Later version\n')
        self.file('literal[1].txt', 'Added\n')
        self.file(' leading [1].txt', 'Literal whitespace\n')
        body = {**self.commit_body('Save all files'), **self.repository_body()}
        result = self.mutate('commit-all', body)
        self.assertEqual(self.git('show', 'HEAD:one.txt'), 'Later version')
        self.assertEqual(self.git('show', 'HEAD:literal[1].txt'), 'Added')
        self.assertEqual(self.git('show', 'HEAD: leading [1].txt'), 'Literal whitespace')
        self.assertFalse(browse_git([self.repo])['changes'])
        self.assertFalse(browse_git([self.repo])['stagedChanges'])
        self.assertEqual(self.mutate('commit-all', body), result)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '1')

    def test_discard_all_keeps_staging_restores_deleted_and_removes_untracked(self):
        self.initialize_history()
        self.file('one.txt', 'Staged\n')
        self.stage('one.txt')
        self.file('one.txt', 'Unstaged\n')
        Path(self.repo, 'two.txt').unlink()
        self.file('nested/new.txt', 'Remove\n')
        before = repository_state(self.repo, False)['indexRevision']
        self.mutate('discard-all', self.repository_body())
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Staged\n')
        self.assertEqual(Path(self.repo, 'two.txt').read_text(), 'Other\n')
        self.assertFalse(Path(self.repo, 'nested/new.txt').exists())
        self.assertEqual(repository_state(self.repo, False)['indexRevision'], before)
        self.assertEqual(len(browse_git([self.repo])['stagedChanges']), 1)

    def test_undo_keeps_index_worktree_and_original_message(self):
        self.initialize_history()
        previous = self.git('rev-parse', 'HEAD')
        self.file('one.txt', 'Committed\n')
        self.stage('one.txt')
        self.mutate('commit', self.commit_body('Last commit\n\nBody'))
        self.file('two.txt', 'Staged extra\n')
        self.stage('two.txt')
        self.file('one.txt', 'Later edits\n')
        index = self.git('ls-files', '--stage')
        response = self.mutate('undo', self.repository_body())
        self.assertEqual(response['message'], 'Last commit\n\nBody')
        self.assertEqual(self.git('rev-parse', 'HEAD'), previous)
        self.assertEqual(self.git('ls-files', '--stage'), index)
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Later edits\n')
        self.assertEqual(len(browse_git([self.repo])['stagedChanges']), 2)

    def test_undo_root_commit_and_reject_shared_commit(self):
        self.initialize_history()
        self.mutate('undo', self.repository_body())
        self.assertNotEqual(git(self.repo, 'rev-parse', '--verify', 'HEAD', local=False).returncode, 0)
        self.assertEqual(len(browse_git([self.repo])['stagedChanges']), 2)
        self.mutate('commit', self.commit_body('New initial commit'))
        self.git('update-ref', 'refs/remotes/origin/main', self.git('rev-parse', 'HEAD'))
        self.assertFalse(browse_git([self.repo])['canUndo'])
        with self.assertRaises(web.HTTPConflict):
            self.mutate('undo', self.repository_body())
        self.assertEqual(self.git('log', '-1', '--format=%s'), 'New initial commit')

    def stash(self, action):
        return self.mutate(action, {**self.repository_body(), 'identity': {'name': 'Test', 'email': 'test@example.com'}})

    def test_stash_tracked_apply_and_pop_keep_untracked_and_latest_order(self):
        self.initialize_history()
        self.file('one.txt', 'Tracked edit\n')
        self.file('untracked.txt', 'Keep me\n')
        self.stash('stash')
        first = browse_git([self.repo])['stashId']
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'First\n')
        self.assertEqual(Path(self.repo, 'untracked.txt').read_text(), 'Keep me\n')
        self.stash('stash-apply')
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Tracked edit\n')
        self.assertEqual(browse_git([self.repo])['stashId'], first)
        self.file('one.txt', 'Second edit\n')
        self.stash('stash')
        second = browse_git([self.repo])['stashId']
        self.assertNotEqual(first, second)
        self.stash('stash-pop')
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Second edit\n')
        self.assertEqual(browse_git([self.repo])['stashId'], first)

    def test_stash_include_untracked_roundtrips_new_files(self):
        self.initialize_history()
        self.file('one.txt', 'Tracked edit\n')
        self.file('nested/new.txt', 'New file\n')
        self.stash('stash-untracked')
        self.assertFalse(Path(self.repo, 'nested/new.txt').exists())
        self.assertFalse(browse_git([self.repo])['changes'])
        self.stash('stash-pop')
        self.assertEqual(Path(self.repo, 'nested/new.txt').read_text(), 'New file\n')
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Tracked edit\n')
        self.assertIsNone(browse_git([self.repo])['stashId'])

    def test_stash_staged_keeps_unstaged_changes(self):
        self.initialize_history()
        self.file('one.txt', 'Keep unstaged\n')
        self.file('two.txt', 'Stash staged\n')
        self.stage('two.txt')
        self.stash('stash-staged')
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Keep unstaged\n')
        self.assertEqual(Path(self.repo, 'two.txt').read_text(), 'Other\n')
        self.assertFalse(browse_git([self.repo])['stagedChanges'])
        self.assertIn('Stash staged', self.git('show', 'refs/stash:two.txt'))

    def test_stash_conflict_keeps_stash_and_reports_conflicts(self):
        self.initialize_history()
        self.file('one.txt', 'Stashed replacement\n')
        self.stash('stash')
        latest = browse_git([self.repo])['stashId']
        self.file('one.txt', 'Committed replacement\n')
        self.stage('one.txt')
        self.mutate('commit', self.commit_body('Different replacement'))
        with self.assertRaises(web.HTTPConflict) as error:
            self.stash('stash-pop')
        self.assertIn('It was kept', error.exception.text)
        self.assertEqual(browse_git([self.repo])['stashId'], latest)
        self.assertTrue(self.git('ls-files', '--unmerged'))

    def test_repository_menu_rejects_stale_worktree_stash_and_active_runs(self):
        self.initialize_history()
        self.file('one.txt', 'Edit\n')
        stale = self.repository_body()
        self.file('one.txt', 'Later\n')
        for action in ('stage-all', 'discard-all', 'undo', 'stash'):
            with self.assertRaises(web.HTTPConflict):
                self.mutate(action, stale)
        with patch('llms.extensions.git.operations.active_run', return_value=True):
            with self.assertRaises(web.HTTPConflict):
                self.stash('stash')
        self.stash('stash')
        body = self.repository_body()
        body['stashId'] = '0' * 40
        with self.assertRaises(web.HTTPConflict):
            self.mutate('stash-pop', body)

    def test_repository_menu_rejects_branch_switch_at_same_commit(self):
        self.initialize_history()
        self.file('one.txt', 'Keep working changes\n')
        reviewed = self.repository_body()
        self.git('branch', 'other')
        self.git('symbolic-ref', 'HEAD', 'refs/heads/other')
        for action in ('stage-all', 'discard-all', 'undo', 'stash'):
            with self.assertRaises(web.HTTPConflict):
                self.mutate(action, reviewed)
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Keep working changes\n')

    def test_bulk_preflight_rejects_filter_before_discarding_any_file(self):
        self.initialize_history()
        self.file('one.txt', 'Keep\n')
        self.file('two.txt', 'Keep too\n')
        self.file('.gitattributes', 'two.txt filter=unsafe\n')
        with self.assertRaises(web.HTTPBadRequest):
            self.mutate('discard-all', self.repository_body())
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Keep\n')
        self.assertEqual(Path(self.repo, 'two.txt').read_text(), 'Keep too\n')
        with self.assertRaises(web.HTTPBadRequest):
            self.stash('stash')

    def test_commit_only_staged_content_and_retry_is_idempotent(self):
        self.stage('one.txt')
        self.file('one.txt', 'Unstaged later\n')
        data = browse_git([self.repo])
        self.assertEqual([c['relativePath'] for c in data['stagedChanges']], ['one.txt'])
        self.assertEqual({c['relativePath'] for c in data['changes']}, {'one.txt', 'two.txt'})
        self.assertIn('+First', diff_git([self.repo], file=str(Path(self.repo, 'one.txt')), staged=True)['patch'])
        body = self.commit_body()
        result = self.mutate('commit', body)
        self.assertEqual(self.git('show', 'HEAD:one.txt'), 'First')
        self.assertEqual(self.git('ls-tree', '--name-only', 'HEAD'), 'one.txt')
        self.assertEqual(self.mutate('commit', body), result)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '1')
        self.assertEqual(self.file('unused.txt', 'Keep').read_text(), 'Keep')

    def test_unstage_unborn_and_existing_repo_preserves_worktree(self):
        self.stage('one.txt')
        self.file('one.txt', 'Later\n')
        self.mutate('unstage', {'paths': ['one.txt']})
        self.assertEqual(self.git('ls-files'), '')
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Later\n')
        self.stage('one.txt')
        self.mutate('commit', self.commit_body())
        self.file('one.txt', 'Third\n')
        self.stage('one.txt')
        self.mutate('unstage', {'paths': ['one.txt']})
        self.assertFalse(browse_git([self.repo])['stagedChanges'])
        self.assertEqual(Path(self.repo, 'one.txt').read_text(), 'Third\n')

    def test_deletions_renames_and_literal_names(self):
        self.file('literal[1].txt', 'Literal\n')
        self.stage('one.txt', 'two.txt', 'literal[1].txt')
        self.mutate('commit', self.commit_body())
        Path(self.repo, 'one.txt').rename(Path(self.repo, 'renamed.txt'))
        Path(self.repo, 'two.txt').unlink()
        self.file('literal[1].txt', 'Changed\n')
        self.stage('one.txt', 'renamed.txt', 'two.txt', 'literal[1].txt')
        changes = browse_git([self.repo])['stagedChanges']
        rename = next(c for c in changes if c['status'] == 'R')
        self.assertEqual(rename['oldRelativePath'], 'one.txt')
        self.file('renamed.txt', 'First\nX\n')
        self.stage('renamed.txt')  # The old name is already absent from the index.
        self.mutate('unstage', {'paths': ['renamed.txt']})
        self.assertEqual(self.git('show', ':one.txt'), 'First')
        self.assertIn('-Other', diff_git([self.repo], file=str(Path(self.repo, 'two.txt')), staged=True)['patch'])

    def test_stale_index_empty_commit_and_missing_identity(self):
        self.stage('one.txt')
        body = self.commit_body()
        self.stage('two.txt')
        with self.assertRaises(web.HTTPConflict):
            self.mutate('commit', body)
        body = self.commit_body()
        body['identity'] = {'name': '', 'email': ''}
        with self.assertRaises(web.HTTPBadRequest):
            self.mutate('commit', body)
        self.mutate('unstage', {'paths': ['one.txt', 'two.txt']})
        with self.assertRaises(web.HTTPBadRequest):
            self.mutate('commit', self.commit_body())

    def test_identity_is_explicit_and_saved_only_to_repo_when_requested(self):
        self.stage('one.txt')
        self.mutate('commit', self.commit_body())
        self.assertEqual(self.git('log', '-1', '--format=%an <%ae>'), 'Test Author <test@example.com>')
        self.assertFalse(repository_state(self.repo, False)['identity']['name'])
        self.stage('two.txt')
        self.mutate('commit', {**self.commit_body(), 'saveIdentity': True})
        self.assertEqual(repository_state(self.repo, False)['identity']['name'], 'Test Author')

    def test_filters_hooks_and_external_paths(self):
        self.file('.gitattributes', '*.txt filter=unsafe\n')
        marker = Path(self.directory.name, 'hook-ran')
        self.git('config', 'filter.unsafe.clean', 'touch ' + str(marker))
        with self.assertRaises(web.HTTPBadRequest):
            self.stage('one.txt')
        self.assertFalse(marker.exists())
        Path(self.repo, '.gitattributes').unlink()
        hook = self.file('.git/hooks/reference-transaction', '#!/bin/sh\ntouch ' + str(marker) + '\n')
        hook.chmod(0o755)
        self.stage('one.txt')
        self.mutate('commit', self.commit_body())
        self.assertFalse(marker.exists())
        for path in ('../outside.txt', '.git/config', '/etc/passwd'):
            with self.assertRaises((web.HTTPBadRequest, web.HTTPForbidden)):
                self.stage(path)
        if os.name != 'nt':
            Path(self.repo, 'escape.txt').symlink_to('/etc/passwd')
            with self.assertRaises(web.HTTPForbidden):
                self.stage('escape.txt')

    def test_commit_validates_staged_files_hidden_by_workspace_boundary(self):
        if os.name == 'nt':
            self.skipTest('Symlink setup requires Unix')
        Path(self.repo, 'escape.txt').symlink_to('/etc/passwd')
        self.git('add', '--', 'escape.txt', 'one.txt')
        with self.assertRaises(web.HTTPForbidden):
            self.mutate('commit', self.commit_body())

    def test_shared_submission_gate_and_active_runs(self):
        with workspace_submission_lock(self.directory.name):
            with self.assertRaises(web.HTTPConflict):
                self.stage('one.txt')
        from llms.extensions.app.db import AppDB
        from tests.test_app_db_provider import MockContext
        path = str(Path(self.directory.name, 'app.sqlite'))
        db = AppDB(MockContext(path), path)
        try:
            thread = db.create_thread({'title': 'Test', 'projectId': 'p'}, 'alice')
            run = db.create_agent_run(thread, 'alice', 'test', workspace={'directories': [self.repo]})
            for status in ('queued', 'running', 'waiting_approval'):
                with db.create_writer_connection() as connection:
                    connection.execute('UPDATE agent_run SET status=? WHERE id=?', (status, run))
                    connection.commit()
                with self.assertRaises(web.HTTPConflict):
                    self.mutate('stage', {'paths': ['one.txt']}, db=db)  # Home detects the captured project workspace.
            with db.create_writer_connection() as connection:
                connection.execute("UPDATE agent_run SET status='completed' WHERE id=?", (run,))
                connection.commit()
            self.mutate('stage', {'paths': ['one.txt']}, db=db)
        finally:
            db.close()

    def test_linked_metadata_and_merge_in_progress_are_rejected(self):
        Path(self.repo, '.git/MERGE_HEAD').write_text('a' * 40)
        with self.assertRaises(web.HTTPConflict):
            self.stage('one.txt')
        Path(self.repo, '.git/MERGE_HEAD').unlink()
        metadata = Path(self.repo, '.git')
        metadata.rename(Path(self.directory.name, 'external-git'))
        metadata.write_text('gitdir: ../external-git\n')
        with self.assertRaises(web.HTTPBadRequest):
            self.stage('one.txt')

    def test_metadata_symlinks_and_existing_index_locks_are_rejected(self):
        self.stage('one.txt')
        lock = Path(self.repo, '.git/index.lock')
        lock.write_text('Existing Git operation')
        with self.assertRaises(web.HTTPConflict):
            self.mutate('commit', self.commit_body())
        self.assertEqual(lock.read_text(), 'Existing Git operation')
        lock.unlink()
        if os.name != 'nt':
            config = Path(self.repo, '.git/config')
            outside = Path(self.directory.name, 'external-config')
            config.rename(outside)
            config.symlink_to(outside)
            with self.assertRaises(web.HTTPForbidden):
                self.stage('two.txt')

    def test_prepared_commit_recovers_after_ref_update_failure(self):
        from llms.extensions.git import operations
        self.stage('one.txt')
        body = self.commit_body()
        original = operations.checked
        def fail_update(repo, *args, **kwargs):
            if args[0] == 'update-ref':
                raise web.HTTPConflict(text='Temporary branch lock')
            return original(repo, *args, **kwargs)
        with patch.object(operations, 'checked', side_effect=fail_update):
            with self.assertRaises(web.HTTPConflict):
                self.mutate('commit', body)
        self.assertFalse(Path(self.repo, '.git/index.lock').exists())
        self.mutate('commit', body)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '1')


class GitMutationRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_authenticated_project_and_home_roots(self):
        ctx = Mock(config={})
        ctx.check_auth.return_value = (True, None)
        ctx.get_username.return_value = 'alice'
        ctx.is_admin.return_value = False
        ctx.is_local = True
        ctx.get_user_path.return_value = '/state'
        ctx.projects.resolve_explorer_workspace.return_value = {'directories': ['/allowed']}
        install(ctx)
        handler = next(call.args[1] for call in ctx.add_post.call_args_list if call.args[0].startswith('repositories/{action'))
        for project in ('p', None):
            body = {'path': '/allowed', 'projectId': project, 'paths': ['one.txt']}
            async def json_body():
                return body
            request = SimpleNamespace(json=json_body, match_info={'action': 'stage'})
            with patch('llms.extensions.git.operations.mutate', return_value={'operation': 'stage'}) as mutation:
                await handler(request)
                mutation.assert_called_once_with(['/allowed'], '/allowed', 'stage', body, data_root='/state',
                    user='alice', project_id=project, db=ctx.app.agent_db, local=True)
            ctx.projects.resolve_explorer_workspace.assert_called_with(project, 'alice', is_admin=False)
        ctx.check_auth.return_value = (False, None)
        ctx.error_auth_required = {}
        self.assertEqual((await handler(request)).status, 401)
