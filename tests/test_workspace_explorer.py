import base64
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from aiohttp import web
from llms.extensions.projects.explorer import browse, install_explorer


class WorkspaceExplorerTests(unittest.TestCase):
    def test_image_formats_return_scoped_data_urls_instead_of_binary_messages(self):
        with tempfile.TemporaryDirectory() as root:
            content = b'\0image bytes\xff'
            for extension, mime in [('png', 'image/png'), ('PNG', 'image/png'),
                                    ('webp', 'image/webp'), ('jpg', 'image/jpeg'), ('jpeg', 'image/jpeg'),
                                    ('gif', 'image/gif'), ('bmp', 'image/bmp'), ('avif', 'image/avif'), ('ico', 'image/x-icon')]:
                with self.subTest(extension=extension):
                    file = Path(root, 'image.' + extension)
                    file.write_bytes(content)
                    preview = browse([root], file=str(file))['file']
                    self.assertEqual(preview['mimeType'], mime)
                    self.assertEqual(preview['image'], f'data:{mime};base64,' + base64.b64encode(content).decode('ascii'))
                    self.assertNotIn('message', preview)
                    self.assertNotIn('content', preview)

    def test_svg_keeps_original_source_and_image_representation(self):
        with tempfile.TemporaryDirectory() as root:
            content = '<svg xmlns="http://www.w3.org/2000/svg"><text>Héllo</text><script>alert(1)</script></svg>'
            file = Path(root, 'image.svg')
            file.write_bytes(content.encode('utf-8'))
            preview = browse([root], file=str(file))['file']
            self.assertEqual(preview['content'], content)
            self.assertEqual(preview['mimeType'], 'image/svg+xml')
            self.assertEqual(base64.b64decode(preview['image'].split(',', 1)[1]), content.encode('utf-8'))

    def test_image_preview_limits_do_not_raise_text_or_svg_limits(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root, 'image.png')
            file.write_bytes(b'\0' * (1024 * 1024 + 1))
            self.assertIn('image', browse([root], file=str(file))['file'])
            with file.open('wb') as stream:
                stream.truncate(10 * 1024 * 1024 + 1)
            preview = browse([root], file=str(file))['file']
            self.assertIn('10 MiB', preview['message'])
            self.assertNotIn('image', preview)
            for extension in ('txt', 'svg'):
                file = Path(root, 'large.' + extension)
                file.write_bytes(b'x' * (1024 * 1024 + 1))
                preview = browse([root], file=str(file))['file']
                self.assertIn('1 MiB', preview['message'])
                self.assertNotIn('image', preview)

    def test_image_paths_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            file = Path(outside, 'private.png')
            file.write_bytes(b'\0private image')
            with self.assertRaises(web.HTTPForbidden):
                browse([root], file=str(file))
            try:
                linked = Path(root, 'escape.png')
                os.symlink(file, linked)
            except OSError:
                self.skipTest('Symlinks unavailable')
            self.assertEqual(browse([root])['entries'], [])
            with self.assertRaises(web.HTTPForbidden):
                browse([root], file=str(linked))

    def test_roots_sorting_and_parent_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'z-folder').mkdir()
            Path(root, 'a.txt').touch()
            data = browse([root])
            self.assertIsNone(data['parent'])
            self.assertEqual([e['name'] for e in data['entries']], ['z-folder', 'a.txt'])
            self.assertEqual(browse([root], os.path.join(root, 'z-folder'))['parent'], root)
            with self.assertRaises(web.HTTPForbidden):
                browse([root], os.path.dirname(root))

    def test_symlink_escape_is_hidden_and_rejected(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            try:
                os.symlink(outside, os.path.join(root, 'escape'))
            except OSError:
                self.skipTest('Symlinks unavailable')
            self.assertEqual(browse([root])['entries'], [])
            with self.assertRaises(web.HTTPForbidden):
                browse([root], os.path.join(root, 'escape'))

    def test_empty_policy(self):
        self.assertEqual(browse([])['roots'], [])

    def test_file_preview_is_bounded_and_scoped(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            file = Path(root, 'hello.txt')
            file.write_text('Hello <script>world</script>')
            self.assertEqual(browse([root], file=str(file))['file']['content'], 'Hello <script>world</script>')
            file.write_bytes(b'\0binary')
            self.assertIn('Binary', browse([root], file=str(file))['file']['message'])
            file.write_bytes(b'x' * (1024 * 1024 + 1))
            self.assertIn('too large', browse([root], file=str(file))['file']['message'])
            with self.assertRaises(web.HTTPForbidden):
                browse([root], file=os.path.join(outside, 'secret.txt'))


class WorkspaceExplorerRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_project_does_not_reuse_legacy_active_project(self):
        ctx = Mock()
        ctx.get_username.return_value = 'alice'
        ctx.is_admin.return_value = False
        ctx.resolve_allowed_directories.return_value = ['/projects/galaga']
        ctx.projects.resolve_explorer_workspace.return_value = {'projectId': None, 'directories': []}
        install_explorer(ctx)
        handler = ctx.add_get.call_args.args[1]
        response = await handler(SimpleNamespace(query={'path': '/projects/galaga'}))
        self.assertEqual(json.loads(response.text)['roots'], [])
        ctx.projects.resolve_explorer_workspace.assert_called_once_with(None, 'alice', is_admin=False)
        ctx.resolve_allowed_directories.assert_not_called()

    async def test_selected_project_is_resolved_for_authenticated_user(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'project-file.txt').touch()
            ctx = Mock()
            ctx.get_username.return_value = 'alice'
            ctx.is_admin.return_value = False
            ctx.projects.resolve_explorer_workspace.return_value = {'projectId': 'project-id', 'directories': [root]}
            install_explorer(ctx)
            handler = ctx.add_get.call_args.args[1]
            response = await handler(SimpleNamespace(query={'projectId': 'project-id'}))
            self.assertEqual(json.loads(response.text)['entries'][0]['name'], 'project-file.txt')
            ctx.projects.resolve_explorer_workspace.assert_called_once_with('project-id', 'alice', is_admin=False)


class WorkspaceExplorerDefaultsTests(unittest.TestCase):
    def test_startup_defaults_survive_legacy_project_switching(self):
        from unittest.mock import MagicMock
        from llms.extensions.projects import install

        with tempfile.TemporaryDirectory() as root:
            ctx = MagicMock()
            ctx.get_user_path.return_value = root
            permissions = {'default': [root, '$TEMP']}
            ctx.get_allowed_directories.side_effect = lambda user=None: permissions.get(user or 'default', [])
            ctx.resolve_directory.side_effect = lambda path: tempfile.gettempdir() if path == '$TEMP' else path
            install(ctx)
            permissions['default'] = [os.path.join(root, 'projects', 'galaga')]
            self.assertEqual(ctx.projects.resolve_explorer_workspace(None)['directories'], [root, tempfile.gettempdir()])
            self.assertEqual(ctx.projects.resolve_workspace(None)['directories'], [])
            # No central workspace: a non-admin without startup directories gets their own private one
            self.assertEqual(ctx.projects.resolve_explorer_workspace(None, 'other-user')['directories'], [os.path.join(root, 'workspace')])
            self.assertTrue(os.path.isdir(os.path.join(root, 'workspace')))
            self.assertEqual(ctx.projects.resolve_explorer_workspace(None, 'admin', is_admin=True)['directories'], [root, tempfile.gettempdir()])
            with self.assertRaises(ValueError):
                ctx.projects.resolve_explorer_workspace('missing-project', 'admin', is_admin=True)


class WorkspaceExplorerAdminRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_admin_gets_startup_defaults(self):
        from unittest.mock import MagicMock
        from llms.extensions.projects import install

        with tempfile.TemporaryDirectory() as root:
            Path(root, 'startup.txt').touch()
            ctx = MagicMock()
            ctx.get_username.return_value = 'admin'
            ctx.is_admin.return_value = True
            users = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, users, True)
            ctx.get_user_path.side_effect = lambda user=None: os.path.join(users, user or 'default')
            permissions = {'default': [root], 'admin': []}
            ctx.get_allowed_directories.side_effect = lambda user=None: permissions.get(user or 'default', [])
            ctx.resolve_directory.side_effect = lambda path: path
            install(ctx)
            permissions['default'] = [os.path.join(root, 'galaga')]
            handler = next(args.args[1] for args in ctx.add_get.call_args_list if args.args[0] == 'explorer')
            response = await handler(SimpleNamespace(query={}))
            self.assertEqual(json.loads(response.text)['path'], root)
            self.assertEqual(json.loads(response.text)['entries'][0]['name'], 'startup.txt')
            ctx.is_admin.return_value = False
            response = await handler(SimpleNamespace(query={}))
            # A non-admin never sees startup directories; they browse their own workspace instead
            self.assertEqual(json.loads(response.text)['roots'], [os.path.realpath(os.path.join(users, 'admin', 'workspace'))])
