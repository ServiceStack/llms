import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aiohttp import web
from llms.extensions.git import browse_git, commit_git, diff_commit_git, diff_git, install


class GitInstallationTests(unittest.TestCase):
    def test_configuration_excludes_git_from_extension_discovery(self):
        import importlib
        main = importlib.import_module('llms.main')

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(main, 'get_extensions_path', return_value=directory), \
                patch.object(main, '_ROOT', Path(main.__file__).parent), \
                patch.object(main, 'g_config', {'disable_extensions': []}):
            self.assertIn('git', [os.path.basename(path) for path in main.get_extensions_dirs()])
            main.g_config['disable_extensions'] = ['git']
            extensions = [os.path.basename(path) for path in main.get_extensions_dirs()]
            self.assertNotIn('git', extensions)
            self.assertIn('projects', extensions)

    def test_missing_git_disables_extension_before_registering_routes(self):
        ctx = SimpleNamespace(disabled=False, config={}, add_get=Mock())
        with patch('llms.extensions.git.shutil.which', return_value=None):
            install(ctx)
        self.assertTrue(ctx.disabled)
        ctx.add_get.assert_not_called()

    def test_disabled_projects_disables_git(self):
        ctx = SimpleNamespace(disabled=False, config={'disable_extensions': ['projects']}, add_get=Mock())
        with patch('llms.extensions.git.shutil.which', return_value='/usr/bin/git'):
            install(ctx)
        self.assertTrue(ctx.disabled)
        ctx.add_get.assert_not_called()


@unittest.skipUnless(shutil.which('git'), 'Git unavailable')
class GitWorkspaceTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', root, *args],
                              check=True, capture_output=True, text=True)

    def commit(self, root, message):
        self.git(root, 'add', '-A')
        self.git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', message)
        return self.git(root, 'rev-parse', 'HEAD').stdout.strip()

    def test_commit_files_and_diffs_use_history_not_worktree(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            file = Path(root, 'literal[1].txt')
            file.write_text('First\n')
            initial = self.commit(root, 'Initial')
            self.assertEqual(commit_git([root], commit=initial)['changes'][0]['status'], 'A')
            self.assertIn('+First\n', diff_commit_git([root], file=str(file), commit=initial)['patch'])
            file.write_text('Second\n')
            second = self.commit(root, 'Second')
            file.write_text('Uncommitted\n')
            diff = diff_commit_git([root], file=str(file), commit=second)
            self.assertEqual(diff['parent'], initial)
            self.assertIn('-First\n+Second\n', diff['patch'])
            self.assertNotIn('Uncommitted', diff['patch'])
            # The same filename at an older revision must still show that revision's diff.
            self.assertIn('+First\n', diff_commit_git([root], file=str(file), commit=initial)['patch'])

    def test_commit_renames_and_deleted_files_with_missing_parents(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            folder = Path(root, 'gone')
            folder.mkdir()
            deleted = folder / 'deleted.txt'
            deleted.write_text('Removed\n')
            original = Path(root, 'original.txt')
            original.write_text('Rename me\n')
            self.commit(root, 'Initial')
            renamed = Path(root, 'renamed space.txt')
            original.rename(renamed)
            deleted.unlink()
            folder.rmdir()
            revision = self.commit(root, 'Rename and delete')
            changes = {c['relativePath']: c for c in commit_git([root], commit=revision)['changes']}
            self.assertEqual(changes['gone/deleted.txt']['status'], 'D')
            self.assertEqual(changes['renamed space.txt']['status'], 'R')
            self.assertEqual(changes['renamed space.txt']['oldRelativePath'], 'original.txt')
            self.assertIn('-Removed\n', diff_commit_git([root], file=str(deleted), commit=revision)['patch'])
            self.assertIn('rename from original.txt', diff_commit_git([root], file=str(renamed), commit=revision)['patch'])

    def test_merge_commit_compares_first_parent(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            Path(root, 'base.txt').write_text('Base\n')
            self.commit(root, 'Base')
            main_branch = self.git(root, 'branch', '--show-current').stdout.strip()
            self.git(root, 'checkout', '-qb', 'side')
            side = Path(root, 'side.txt')
            side.write_text('Side\n')
            self.commit(root, 'Side')
            self.git(root, 'checkout', '-q', main_branch)
            Path(root, 'main.txt').write_text('Main\n')
            first_parent = self.commit(root, 'Main')
            self.git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                     'merge', '--no-ff', '-qm', 'Merge', 'side')
            revision = self.git(root, 'rev-parse', 'HEAD').stdout.strip()
            data = commit_git([root], commit=revision)
            self.assertEqual(data['parent'], first_parent)
            self.assertEqual([change['relativePath'] for change in data['changes']], ['side.txt'])
            self.assertIn('+Side\n', diff_commit_git([root], file=str(side), commit=revision)['patch'])

    def test_commit_previews_bound_files_and_disable_external_drivers(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            binary = Path(root, 'binary.bin')
            binary.write_bytes(b'\0Binary')
            large = Path(root, 'large.txt')
            large.write_bytes(b'x' * (1024 * 1024 + 1))
            text = Path(root, 'sample.txt')
            text.write_text('Readable\n')
            Path(root, '.gitattributes').write_text('*.txt diff=custom\n')
            revision = self.commit(root, 'Files')
            self.git(root, 'config', 'diff.external', 'false')
            self.git(root, 'config', 'diff.custom.textconv', 'false')
            self.assertIn('Binary', diff_commit_git([root], file=str(binary), commit=revision)['message'])
            self.assertIn('too large', diff_commit_git([root], file=str(large), commit=revision)['message'])
            self.assertIn('+Readable\n', diff_commit_git([root], file=str(text), commit=revision)['patch'])

    def test_commit_hash_and_workspace_validation(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            self.git(root, 'init', '-q')
            file = Path(root, 'file.txt')
            file.write_text('Allowed\n')
            revision = self.commit(root, 'Initial')
            for invalid in ('HEAD', '--all', revision + '^', ''):
                with self.assertRaises(web.HTTPBadRequest):
                    commit_git([root], commit=invalid)
            with self.assertRaises(web.HTTPNotFound):
                commit_git([root], commit='0' * 40)
            with self.assertRaises(web.HTTPForbidden):
                commit_git([root], path=outside, commit=revision)
            with self.assertRaises(web.HTTPNotFound):
                diff_commit_git([root], file=str(Path(outside, 'secret.txt')), commit=revision)
            with self.assertRaises(web.HTTPNotFound):
                diff_commit_git([root], file=str(Path(root, 'unchanged.txt')), commit=revision)
            try:
                file.unlink()
                os.symlink(Path(outside, 'secret.txt'), file)
            except OSError:
                self.skipTest('Symlinks unavailable')
            self.assertEqual(commit_git([root], commit=revision)['changes'], [])
            with self.assertRaises(web.HTTPNotFound):
                diff_commit_git([root], file=str(file), commit=revision)

    def test_history_discovery_is_bounded_by_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            self.git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                     'commit', '--allow-empty', '-qm', 'First commit\n\nDetails\nMore details')
            nested = os.path.join(root, 'nested')
            os.mkdir(nested)
            data = browse_git([root], nested)
            self.assertEqual(data['repository'], root)
            self.assertEqual(data['commits'][0]['subject'], 'First commit')
            self.assertIn('More details', data['commits'][0]['body'])
            self.assertEqual(data['commits'][0]['email'], 'test@example.com')
            self.assertIsNone(browse_git([nested])['repository'])
            with self.assertRaises(web.HTTPForbidden):
                browse_git([nested], root)

    def test_new_repo_changes_include_worktree_and_untracked_only(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            Path(root, 'staged.txt').write_text('Staged')
            Path(root, 'modified.txt').write_text('Original')
            self.git(root, 'add', '.')
            Path(root, 'modified.txt').write_text('Modified')
            Path(root, 'untracked space.txt').write_text('Untracked')
            data = browse_git([root])
            self.assertEqual(data['commits'], [])
            self.assertEqual({c['relativePath']: c['status'] for c in data['changes']},
                             {'modified.txt': 'M', 'untracked space.txt': '?'})

    def test_symlink_escape_is_rejected_and_hidden_in_changes(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            self.git(root, 'init', '-q')
            try:
                os.symlink(outside, os.path.join(root, 'escape'))
            except OSError:
                self.skipTest('Symlinks unavailable')
            with self.assertRaises(web.HTTPForbidden):
                browse_git([root], os.path.join(root, 'escape'))
            self.assertEqual(browse_git([root])['changes'], [])

    def test_no_repository_and_empty_permissions(self):
        self.assertIsNone(browse_git([])['repository'])
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(browse_git([root])['repository'])

    def test_diff_compares_index_to_worktree_not_head(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            file = Path(root, 'sample.txt')
            file.write_text('Committed\nKeep\n')
            self.git(root, 'add', '.')
            self.git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Initial')
            file.write_text('Staged\nKeep\n')
            self.git(root, 'add', '.')
            file.write_text('Unstaged\nKeep\nAdded\n')
            patch = diff_git([root], file=str(file))['patch']
            self.assertIn('-Staged\n', patch)
            self.assertIn('+Unstaged\n', patch)
            self.assertIn(' Keep\n', patch)
            self.assertIn('+Added\n', patch)
            self.assertNotIn('Committed', patch)
            file.write_text('Staged\nKeep\n')
            self.assertIn('No unstaged', diff_git([root], file=str(file))['message'])

    def test_deleted_file_with_missing_parent_has_removed_lines(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            folder = Path(root, 'deleted')
            folder.mkdir()
            file = folder / 'old.txt'
            file.write_text('Removed\n')
            self.git(root, 'add', '.')
            file.unlink()
            folder.rmdir()
            self.assertIn('-Removed\n', diff_git([root], root, str(file))['patch'])

    def test_untracked_file_and_no_final_newline(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            file = Path(root, 'new file.txt')
            file.write_text('First\nLast')
            patch = diff_git([root], file=str(file))['patch']
            self.assertIn('--- /dev/null\n', patch)
            self.assertIn('+First\n+Last\n\\ No newline at end of file\n', patch)
            file.write_text('')
            self.assertIn('New empty file', diff_git([root], file=str(file))['message'])

    def test_diff_rejects_outside_paths_symlinks_and_missing_repository(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            self.git(root, 'init', '-q')
            file = Path(outside, 'secret.txt')
            file.write_text('Secret')
            with self.assertRaises(web.HTTPForbidden):
                diff_git([root], file=str(file))
            with self.assertRaises(web.HTTPForbidden):
                diff_git([], file=str(file))
            with self.assertRaises(web.HTTPBadRequest):
                diff_git([root])
            with self.assertRaises(web.HTTPBadRequest):
                diff_git([outside], file=str(file))
            with self.assertRaises(web.HTTPNotFound):
                diff_git([root], file=str(Path(root, 'missing.txt')))
            try:
                os.symlink(file, Path(root, 'escape.txt'))
            except OSError:
                self.skipTest('Symlinks unavailable')
            with self.assertRaises(web.HTTPForbidden):
                diff_git([root], file=str(Path(root, 'escape.txt')))

    def test_binary_and_large_files_return_messages(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            file = Path(root, 'binary.txt')
            file.write_bytes(b'\0Binary')
            self.git(root, 'add', '.')
            file.write_text('Now text\n')
            self.assertIn('Binary', diff_git([root], file=str(file))['message'])
            file.write_bytes(b'x' * (1024 * 1024 + 1))
            self.assertIn('too large', diff_git([root], file=str(file))['message'])
            self.git(root, 'add', '.')
            file.write_text('Small\n')
            self.assertIn('too large', diff_git([root], file=str(file))['message'])

    def test_literal_path_and_external_diff_drivers(self):
        with tempfile.TemporaryDirectory() as root:
            self.git(root, 'init', '-q')
            file = Path(root, 'literal[1].txt')
            file.write_text('Before\n')
            Path(root, '.gitattributes').write_text('*.txt diff=custom\n')
            self.git(root, 'add', '.')
            self.git(root, 'config', 'diff.external', 'false')
            self.git(root, 'config', 'diff.custom.textconv', 'false')
            file.write_text('After\n')
            patch = diff_git([root], file=str(file))['patch']
            self.assertIn('-Before\n+After\n', patch)


class GitRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_commit_routes_use_authenticated_project_roots(self):
        ctx = Mock(config={})
        ctx.get_username.return_value = 'alice'
        ctx.is_admin.return_value = False
        ctx.projects.resolve_explorer_workspace.return_value = {'directories': ['/allowed']}
        with patch('llms.extensions.git.shutil.which', return_value='/usr/bin/git'):
            install(ctx)
        routes = {call.args[0]: call.args[1] for call in ctx.add_get.call_args_list}
        query = {'projectId': 'p', 'path': '/allowed', 'file': '/allowed/a.txt', 'commit': 'a' * 40}
        for route, reader, args in [('commit', 'commit_git', ('a' * 40,)),
                                    ('diff', 'diff_commit_git', ('/allowed/a.txt', 'a' * 40))]:
            with patch('llms.extensions.git.' + reader, return_value={}) as mocked:
                await routes[route](SimpleNamespace(query=query))
                mocked.assert_called_once_with(['/allowed'], '/allowed', *args)
        ctx.projects.resolve_explorer_workspace.assert_called_with('p', 'alice', is_admin=False)

    async def test_resolves_authenticated_workspace_and_explicit_project(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = Mock(config={})
            ctx.get_username.return_value = 'alice'
            ctx.is_admin.return_value = False
            ctx.projects.resolve_explorer_workspace.return_value = {'directories': [root]}
            with patch('llms.extensions.git.shutil.which', return_value='/usr/bin/git'):
                install(ctx)
            handler = next(call.args[1] for call in ctx.add_get.call_args_list if call.args[0] == 'workspace')
            response = await handler(SimpleNamespace(query={'projectId': 'p'}))
            self.assertIsNone(json.loads(response.text)['repository'])
            ctx.projects.resolve_explorer_workspace.assert_called_once_with('p', 'alice', is_admin=False)

    async def test_invalid_project_is_rejected(self):
        ctx = Mock(config={})
        ctx.projects.resolve_explorer_workspace.side_effect = ValueError('Project not found')
        with patch('llms.extensions.git.shutil.which', return_value='/usr/bin/git'):
            install(ctx)
        with self.assertRaises(web.HTTPBadRequest):
            await ctx.add_get.call_args.args[1](SimpleNamespace(query={'projectId': 'missing'}))

    async def test_diff_uses_authenticated_project_roots(self):
        ctx = Mock(config={})
        ctx.get_username.return_value = 'alice'
        ctx.is_admin.return_value = False
        ctx.projects.resolve_explorer_workspace.return_value = {'directories': ['/allowed']}
        with patch('llms.extensions.git.shutil.which', return_value='/usr/bin/git'):
            install(ctx)
        handler = next(call.args[1] for call in ctx.add_get.call_args_list if call.args[0] == 'diff')
        with patch('llms.extensions.git.diff_git', return_value={'patch': 'example'}) as reader:
            response = await handler(SimpleNamespace(query={'projectId': 'p', 'path': '/allowed', 'file': '/allowed/a.txt'}))
        self.assertEqual(json.loads(response.text)['patch'], 'example')
        reader.assert_called_once_with(['/allowed'], '/allowed', '/allowed/a.txt', False)
        ctx.projects.resolve_explorer_workspace.assert_called_once_with('p', 'alice', is_admin=False)
