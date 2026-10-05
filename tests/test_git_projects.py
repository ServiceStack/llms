import asyncio
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from aiohttp import web
from llms.extensions.git.provision import GitProvisioner, repository_source
from llms.extensions.projects import install


class GitSourceTests(unittest.TestCase):
    def test_accepts_https_and_ssh_without_credentials(self):
        for url in ('https://github.com/owner/repo', 'ssh://git@example.org/owner/repo.git',
                    'git@example.org:owner/repo.git'):
            self.assertEqual(repository_source({'url': url})['url'], url)

    def test_rejects_credentials_unsafe_transports_and_invalid_refs(self):
        for url in ('-c', 'file:///tmp/repo', '/tmp/repo', 'ext::sh command',
                    'https://token@github.com/a/b', 'https://a:secret@example.org/a',
                    'https://github.com/a/b/issues/1', 'https://host:bad/a',
                    'https://host/a?token=secret'):
            with self.subTest(url=url), self.assertRaises(web.HTTPBadRequest):
                repository_source({'url': url})
        for branch in ('--upload-pack=evil', '../main', 'a b', 'main.lock', 'a@{b'):
            with self.subTest(branch=branch), self.assertRaises(web.HTTPBadRequest):
                repository_source({'url': 'https://github.com/a/b', 'branch': branch})

    def test_hosted_clones_do_not_use_machine_ssh_or_arbitrary_hosts(self):
        provider = GitProvisioner('git')
        for url in ('git@github.com:a/b.git', 'https://localhost/repo', 'https://private.example/repo'):
            with self.assertRaises(web.HTTPBadRequest):
                provider.validate({'url': url})


class ProjectCreationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.routes, self.cleanup = {}, []

        def user_path(user=None):
            return str(Path(self.directory.name, 'user', user or 'default'))

        self.ctx = SimpleNamespace(
            app=SimpleNamespace(git_provisioner=None), get_user_path=user_path,
            get_username=lambda request: request.user, get_allowed_directories=lambda user=None: [],
            get_user_pref=lambda *a, **kw: None, set_user_pref=Mock(), set_allowed_directories=Mock(),
            resolve_directory=lambda p: p, register_setup_user_handler=Mock(),
            register_startup_handler=Mock(), register_cleanup_handler=self.cleanup.append,
            notify_sidebar=Mock(), log=Mock(), err=Mock(),
            add_get=lambda path, fn: self.routes.update({('GET', path): fn}),
            add_post=lambda path, fn: self.routes.update({('POST', path): fn}),
            add_patch=lambda path, fn: self.routes.update({('PATCH', path): fn}),
        )
        install(self.ctx)
        self.service = self.cleanup[0].__self__

    async def asyncTearDown(self):
        await self.service.close()
        self.directory.cleanup()

    def payload(self, *, name='Example', folder='example', source=None, request_id=None):
        return {'requestId': request_id or str(uuid.uuid4()), 'project': {'name': name, 'folder': folder},
                'source': source or {'kind': 'new', 'initializeGit': False}}

    async def finish(self, operation, user=None):
        for _ in range(200):
            row = self.service.read(user, operation['id'])
            if row['state'] in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                return self.service.snapshot(row)
            await asyncio.sleep(.025)
        self.fail('Creation did not complete')

    async def test_plain_creation_is_registered_once_and_notifies_sidebar(self):
        payload = self.payload()
        operation = await self.service.create(None, payload)
        repeated = await self.service.create(None, payload)
        self.assertEqual(operation['id'], repeated['id'])
        result = await self.finish(operation)
        self.assertEqual(result['state'], 'succeeded', result)
        project = result['result']['project']
        folder = Path(self.ctx.get_user_path(), 'projects', 'example')
        self.assertTrue(folder.is_dir())
        self.assertFalse((folder / '.git').exists())
        self.assertEqual(list(folder.iterdir()), [])
        self.assertEqual(self.ctx.projects.get_user_projects()[0]['id'], project['id'])
        self.ctx.notify_sidebar.assert_called_once()
        with self.assertRaises(web.HTTPConflict):
            await self.service.create(None, self.payload())

    async def test_unavailable_git_has_explicit_opt_out(self):
        for source in ({'kind': 'new', 'initializeGit': True}, {'kind': 'clone', 'url': 'https://github.com/a/b'}):
            with self.assertRaises(web.HTTPServiceUnavailable):
                await self.service.create(None, self.payload(source=source))
        result = await self.finish(await self.service.create(None, self.payload()))
        self.assertEqual(result['state'], 'succeeded')

    async def test_validation_destination_conflicts_and_user_isolation(self):
        for folder in ('../escape', '/tmp/escape', 'a/b', '.hidden', 'NUL', 'folder.', 'folder '):
            with self.subTest(folder=folder), self.assertRaises(web.HTTPBadRequest):
                await self.service.create(None, self.payload(folder=folder))
        root = Path(self.ctx.get_user_path(), 'projects')
        root.mkdir(parents=True, exist_ok=True)
        (root / 'example').mkdir()
        (root / 'example' / 'keep.txt').write_text('Keep')
        with self.assertRaises(web.HTTPConflict):
            await self.service.create(None, self.payload())
        self.assertEqual((root / 'example' / 'keep.txt').read_text(), 'Keep')
        operation = await self.service.create('alice', self.payload())
        await self.finish(operation, 'alice')
        with self.assertRaises(web.HTTPNotFound):
            self.service.read('bob', operation['id'])
        self.assertEqual(self.ctx.projects.get_user_projects('bob'), [])

    async def test_request_reuse_and_pending_destinations_are_serialized(self):
        payload = self.payload()
        operation = await self.service.create(None, payload)
        with self.assertRaises(web.HTTPConflict):
            await self.service.create(None, {**payload, 'project': {'name': 'Other', 'folder': 'other'}})
        with self.assertRaises(web.HTTPConflict):
            await self.service.create(None, self.payload(name='Other'))
        await self.finish(operation)

    async def test_finalization_failure_retains_workspace_and_retry_preserves_id(self):
        import llms.extensions.projects as projects
        original = projects.atomic_projects
        with patch.object(projects, 'atomic_projects', side_effect=OSError('Disk failure')):
            operation = await self.service.create(None, self.payload())
            failed = await self.finish(operation)
        self.assertEqual(failed['state'], 'failed')
        folder = Path(self.ctx.get_user_path(), 'projects', 'example')
        self.assertTrue(folder.exists())
        with patch.object(projects, 'atomic_projects', wraps=original):
            await self.service.retry(None, operation['id'])
            result = await self.finish(operation)
        self.assertEqual(result['state'], 'succeeded', result)
        self.assertEqual(result['result']['project']['id'], failed['project']['id'])
        self.assertFalse((folder / '.llms-creation').exists())

    async def test_cancel_cleans_only_job_owned_temporary_folder(self):
        class SlowProvider:
            def validate(self, source):
                return source

            async def provision(self, path, source, progress, cancelled, child):
                Path(path).mkdir()
                while not cancelled():
                    await asyncio.sleep(.01)
                raise asyncio.CancelledError

        self.ctx.app.git_provisioner = SlowProvider()
        operation = await self.service.create(None, self.payload(source={'kind': 'clone', 'url': 'https://github.com/a/b'}))
        await asyncio.sleep(.05)
        request = SimpleNamespace(user=None, match_info={'id': operation['id']})
        await self.routes['POST', 'creation/operations/{id}/cancel'](request)
        result = await self.finish(operation)
        self.assertEqual(result['state'], 'cancelled')
        root = Path(self.ctx.get_user_path(), 'projects')
        self.assertFalse((root / 'example').exists())
        self.assertFalse((root / ('.create-' + operation['id'])).exists())
        self.assertEqual(self.ctx.projects.get_user_projects(), [])

    async def test_reopened_dialog_reads_progress_and_foreign_user_cannot_cancel(self):
        with patch.object(self.service, 'schedule'):
            operation = await self.service.create('alice', self.payload())
        foreign = SimpleNamespace(user='bob', match_info={'id': operation['id']}, query={})
        with self.assertRaises(web.HTTPNotFound):
            await self.routes['GET', 'creation/operations/{id}'](foreign)
        with self.assertRaises(web.HTTPNotFound):
            await self.routes['POST', 'creation/operations/{id}/cancel'](foreign)
        response = await self.routes['GET', 'creation/options'](SimpleNamespace(user='alice'))
        self.assertIn(operation['id'], response.text)
        result = await self.finish(operation, 'alice')
        self.assertEqual(result['state'], 'succeeded')

    async def test_completed_provenance_survives_metadata_edit_and_bulk_save(self):
        source = {'kind': 'clone', 'url': 'https://github.com/example/repo'}
        class Provider:
            def validate(self, value):
                return value
            async def provision(self, path, *args):
                Path(path).mkdir()
        self.ctx.app.git_provisioner = Provider()
        result = await self.finish(await self.service.create(None, self.payload(source=source)))
        self.assertEqual(result['state'], 'succeeded')
        async def data():
            return {'name': 'Renamed', 'folder': 'example', 'description': 'Edited'}
        await self.routes['POST', 'save/{name}'](SimpleNamespace(user=None, match_info={'name': 'Example'}, json=data))
        project = self.ctx.projects.get_user_projects()[0]
        self.assertEqual(project['gitSource'], source)
        project.pop('gitSource')
        async def bulk():
            return [project]
        await self.routes['POST', 'projects.json'](SimpleNamespace(user=None, json=bulk))
        self.assertEqual(self.ctx.projects.get_user_projects()[0]['gitSource'], source)

    async def test_repository_metadata_symlink_cannot_escape_during_finalization(self):
        outside = Path(self.directory.name, 'outside.txt')
        outside.write_text('Keep')
        class Provider:
            def validate(self, value):
                return value
            async def provision(self, path, *args):
                Path(path).mkdir()
                Path(path, '.llms-creation').symlink_to(outside)
        self.ctx.app.git_provisioner = Provider()
        result = await self.finish(await self.service.create(None, self.payload(source={'kind': 'clone', 'url': 'https://github.com/a/b'})))
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(outside.read_text(), 'Keep')
        self.assertEqual(self.ctx.projects.get_user_projects(), [])

    async def test_expired_worker_is_interrupted_and_retried_safely(self):
        payload = self.payload()
        # Persist queued work without letting the first service start it.
        with patch.object(self.service, 'schedule'):
            operation = await self.service.create(None, payload)
        self.service.update(None, operation['id'], state='running', owner='old-server', lease=0)
        await self.service.recover(None)
        interrupted = await self.finish(operation)
        self.assertEqual(interrupted['state'], 'interrupted')
        await self.service.retry(None, operation['id'])
        result = await self.finish(operation)
        self.assertEqual(result['state'], 'succeeded', result)

    async def test_restart_reconciles_prepared_workspace_without_recreating_it(self):
        import llms.extensions.projects as projects
        with patch.object(projects, 'atomic_projects', side_effect=OSError('Disk failure')):
            operation = await self.service.create(None, self.payload())
            failed = await self.finish(operation)
        await asyncio.sleep(0)
        folder = Path(self.ctx.get_user_path(), 'projects', 'example')
        (folder / 'keep.txt').write_text('Preserve completed workspace')
        self.service.update(None, operation['id'], state='finalizing', owner='previous-server', lease=0)
        await self.service.recover(None)
        result = await self.finish(operation)
        self.assertEqual(result['state'], 'succeeded', result)
        self.assertEqual(result['result']['project']['id'], failed['project']['id'])
        self.assertEqual((folder / 'keep.txt').read_text(), 'Preserve completed workspace')

    @unittest.skipUnless(shutil.which('git'), 'Git unavailable')
    async def test_git_initialization_uses_main_without_identity_or_fake_commit(self):
        self.ctx.app.git_provisioner = GitProvisioner(shutil.which('git'), local=False)
        operation = await self.service.create(None, self.payload(source={'kind': 'new', 'initializeGit': True}))
        result = await self.finish(operation)
        self.assertEqual(result['state'], 'succeeded', result)
        folder = Path(self.ctx.get_user_path(), 'projects', 'example')
        def git(*args):
            return subprocess.run(['git', '-C', str(folder), *args], capture_output=True, text=True)
        self.assertEqual(git('symbolic-ref', '--short', 'HEAD').stdout.strip(), 'main')
        self.assertNotEqual(git('rev-parse', '--verify', 'HEAD').returncode, 0)
        self.assertEqual(git('status', '--porcelain').stdout, '')

    @unittest.skipUnless(shutil.which('git'), 'Git unavailable')
    async def test_clone_preserves_history_branch_and_remote_with_real_git(self):
        remote = Path(self.directory.name, 'remote')
        remote.mkdir()
        def git(*args):
            subprocess.run(['git', '-C', str(remote), *args], check=True, capture_output=True)
        git('init', '-q')
        (remote / 'hello.txt').write_text('Hello\n')
        git('add', 'hello.txt')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Initial')
        git('checkout', '-qb', 'feature')
        (remote / 'hello.txt').write_text('Feature\n')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qam', 'Feature')
        self.ctx.app.git_provisioner = GitProvisioner(shutil.which('git'), local=False)
        original = asyncio.create_subprocess_exec
        async def local_transport(*args, **kwargs):
            # Substitute only the transport in tests; production rejects local/file URLs.
            args = list(args)
            if 'clone' in args:
                args[args.index('https://github.com/example/repo')] = str(remote)
                args[1:1] = ['-c', 'protocol.file.allow=always']
            return await original(*args, **kwargs)
        with patch('llms.extensions.git.provision.asyncio.create_subprocess_exec', local_transport):
            operation = await self.service.create(None, self.payload(source={'kind': 'clone', 'url': 'https://github.com/example/repo', 'branch': 'feature'}))
            result = await self.finish(operation)
        self.assertEqual(result['state'], 'succeeded', result)
        folder = Path(self.ctx.get_user_path(), 'projects', 'example')
        self.assertEqual((folder / 'hello.txt').read_text(), 'Feature\n')
        commits = subprocess.check_output(['git', '-C', str(folder), 'rev-list', '--count', 'HEAD'], text=True)
        self.assertEqual(commits.strip(), '2')
        self.assertEqual(result['result']['project']['gitSource']['branch'], 'feature')
        self.assertEqual(self.ctx.projects.get_user_projects()[0]['gitSource']['url'], 'https://github.com/example/repo')
