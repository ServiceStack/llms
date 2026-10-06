import argparse
import functools
import http.server
import importlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.request import urlopen

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from llms.extensions.projects import install as install_projects
from llms.extensions.share_static import install
from llms.extensions.share_static.static import publish_folder, rewrite_index, settings


class StaticPublishTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ctx = MagicMock()
        self.ctx.config = {}
        self.ctx.app.aliased_directories = {"$WORKSPACE": str(self.root)}
        self.ctx.get_user_path.side_effect = lambda user=None: str(self.root / "users" / (user or "default"))
        self.ctx.get_allowed_directories.return_value = []
        self.ctx.check_auth.return_value = (True, None)
        self.ctx.get_username.return_value = "alice"
        self.ctx.resolve_directory.side_effect = os.path.abspath
        install_projects(self.ctx)
        self.create_project("alice")
        install(self.ctx)
        self.handler = next(c.args[1] for c in self.ctx.add_post.call_args_list if c.args[0] == "project/{id}/folder")
        self.request = MagicMock(match_info={"id": "project-id"})
        self.config = settings({}, str(self.root))
        self.source = self.root / "users/alice/projects/my-project/dist"
        self.destination = self.root / "p/alice/my-project"

    def create_project(self, user, project_id="project-id"):
        root = Path(self.ctx.get_user_path(user)) / "projects"
        output = root / "my-project/dist"
        output.mkdir(parents=True)
        (output / "index.html").write_text(
            '<!doctype html><html><head><title>Demo</title></head><body><script src="/assets/app.js"></script></body></html>'
        )
        (output / "assets").mkdir()
        (output / "assets/app.js").write_bytes(b'console.log("standalone")')
        (output / "empty").mkdir()
        (root / "projects.json").write_text(
            json.dumps(
                [
                    {
                        "id": project_id,
                        "name": "My Project",
                        "folder": "my-project",
                        "publish": "dist",
                        "publishedUrl": "https://ai.llmspy.org/p/alice/remote",
                    }
                ]
            )
        )

    def export(self, user="alice", config=None):
        return publish_folder(self.ctx, user, "project-id", config or self.config)

    async def test_account_free_endpoint_and_startup_directory(self):
        with patch("aiohttp.ClientSession") as network:
            response = await self.handler(self.request)
            network.assert_not_called()
        result = json.loads(response.text)
        self.assertEqual(result["publishedPath"], str(self.destination))
        self.assertEqual(result["urlPath"], "/p/alice/my-project/")
        self.assertIsNone(result["publishedUrl"])
        self.assertIn('<base href="/p/alice/my-project/">', (self.destination / "index.html").read_text())
        self.assertIn('src="assets/app.js"', (self.destination / "index.html").read_text())
        self.assertIn('src="/assets/app.js"', (self.source / "index.html").read_text())
        self.assertEqual(
            (self.source / "assets/app.js").read_bytes(), (self.destination / "assets/app.js").read_bytes()
        )
        self.assertTrue((self.destination / "empty").is_dir())
        project = self.ctx.projects.get_user_projects("alice")[0]
        self.assertEqual(project["staticPublication"], result)
        self.assertEqual(project["publishedUrl"], "https://ai.llmspy.org/p/alice/remote")
        self.assertFalse((self.destination / ".projects.lock").exists())

    async def test_extension_loader_registers_configuration_before_static_ui_route(self):
        main = importlib.import_module("llms.main")

        extension = Path(__file__).resolve().parents[1] / "llms/extensions/share_static"
        with (
            patch.object(main, "g_app"),
            patch.object(main, "get_extensions_dirs", return_value=[str(extension)]),
            patch.object(main, "get_disabled_extensions", return_value=[]),
        ):
            app = main.AppExtensions(argparse.Namespace(), {})
            app.config = {}
            app.get_user_path = self.ctx.get_user_path
            app.aliased_directories = {"$WORKSPACE": str(self.root)}
            app.extensions = main.install_extensions()
            self.assertEqual([item["name"] for item in app.extensions], ["share_static"])
            self.assertEqual(app.ui_extensions, [{"id": "share_static", "path": "/ext/share_static/index.mjs"}])
            server = web.Application()
            for path, handler, kwargs in app.server_add_get:
                server.router.add_get(path, handler, **kwargs)
            for path, handler, kwargs in app.server_add_post:
                server.router.add_post(path, handler, **kwargs)
            self.assertIn("/ext/share_static/project/{id}/folder", [route[0] for route in app.server_add_post])
            async with TestClient(TestServer(server)) as client:
                response = await client.get("/ext/share_static/config.json")
                self.assertEqual(response.status, 200)
                config = await response.json()
                self.assertTrue(config["enabled"])
                self.assertEqual(config["directory"], str(self.root / "p"))
                self.assertEqual((await client.get("/ext/share_static/index.mjs")).status, 200)

    async def test_authentication_is_required_when_host_requires_it(self):
        self.ctx.check_auth.return_value = (False, None)
        with self.assertRaises(web.HTTPUnauthorized):
            await self.handler(self.request)
        self.assertFalse(self.destination.exists())

    def test_disabled_and_custom_configuration(self):
        config = settings({"enabled": False}, str(self.root))
        with self.assertRaises(web.HTTPForbidden):
            self.export(config=config)
        config = settings(
            {
                    "directory": "www/p",
                    "basePath": "/exports/",
                    "baseUrl": "https://ubixar.com/exports/",
                },
            str(self.root),
        )
        result = self.export(config=config)
        self.assertEqual(result["publishedPath"], str(self.root / "www/p/alice/my-project"))
        self.assertEqual(result["publishedUrl"], "https://ubixar.com/exports/alice/my-project/")
        for base in ("relative", "//host/", "/p/../", "/p/?query", "/p/#fragment"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                settings({"basePath": base}, str(self.root))

    def test_optional_base_url_and_mount_path(self):
        for value in (None, ""):
            config = settings({"baseUrl": value}, str(self.root))
            result = self.export(config=config)
            self.assertIsNone(result["publishedUrl"])
            self.assertEqual(result["urlPath"], "/p/alice/my-project/")
        config = settings({"baseUrl": "https://example.com/sites/"}, str(self.root))
        result = self.export(config=config)
        self.assertEqual(result["publishedUrl"], "https://example.com/sites/alice/my-project/")
        self.assertEqual(result["urlPath"], "/sites/alice/my-project/")
        self.assertIn('<base href="/sites/alice/my-project/">', (self.destination / "index.html").read_text())
        for value in (
            "relative",
            "javascript:alert(1)",
            "https://host/p?query",
            "https://host/p#fragment",
            "https://user:pass@host/p",
            "https://host/a/../p",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings({"baseUrl": value}, str(self.root))

    async def test_extension_global_config_defaults_and_override(self):
        get = next(c.args[1] for c in self.ctx.add_get.call_args_list if c.args[0] == "config.json")
        default = json.loads((await get(self.request)).text)
        self.assertTrue(default["enabled"])
        self.assertEqual(default["directory"], str(self.root / "p"))
        self.assertEqual(default["baseUrl"], "")
        path = self.root / "users/default/share_static/config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"directory": "./sites", "baseUrl": "https://static.example/output"}))
        self.ctx.add_get.reset_mock()
        install(self.ctx)
        get = next(c.args[1] for c in self.ctx.add_get.call_args_list if c.args[0] == "config.json")
        config = json.loads((await get(self.request)).text)
        self.assertEqual(config["directory"], str(self.root / "sites"))
        self.assertEqual(config["basePath"], "/output/")
        # No account endpoints or account store exist in static sharing.
        self.assertFalse(any(c.args[0] in ("config.json", "disconnect") for c in self.ctx.add_post.call_args_list))

    def test_republish_removes_obsolete_files(self):
        (self.source / "old.txt").write_text("old")
        self.export()
        (self.source / "old.txt").unlink()
        (self.source / "new.txt").write_text("new")
        self.export()
        self.assertFalse((self.destination / "old.txt").exists())
        self.assertEqual((self.destination / "new.txt").read_text(), "new")

    def test_hidden_files_and_folders_are_not_published(self):
        (self.source / ".git/refs").mkdir(parents=True)
        (self.source / ".git/HEAD").write_text("ref: refs/heads/main")
        # A link in a hidden folder would otherwise be rejected
        (self.source / ".git/refs/outside").symlink_to(self.root, target_is_directory=True)
        (self.source / ".env").write_text("API_KEY=secret")
        (self.source / "assets/.git").write_text("gitdir: ../../.git/worktrees/site")
        (self.source / "assets/.cache").mkdir()
        (self.source / "assets/.cache/build.json").write_text("{}")
        (self.source / "assets/app.v1.js").write_text("app()")
        self.export()
        self.assertFalse((self.destination / ".git").exists())
        self.assertFalse((self.destination / ".env").exists())
        self.assertFalse((self.destination / "assets/.git").exists())
        self.assertFalse((self.destination / "assets/.cache").exists())
        # Only names that start with '.' are hidden
        self.assertEqual((self.destination / "assets/app.v1.js").read_text(), "app()")
        self.assertTrue((self.destination / "assets/app.js").exists())
        # The source is unchanged
        self.assertTrue((self.source / ".env").exists())

    def test_failed_copy_or_rewrite_preserves_publication(self):
        previous = self.export()
        original = (self.destination / "index.html").read_bytes()
        for target in ("copy_output", "rewrite_index"):
            with (
                self.subTest(target=target),
                patch("llms.extensions.share_static.static." + target, side_effect=OSError("failed")),
                self.assertRaises(OSError),
            ):
                self.export()
            self.assertEqual((self.destination / "index.html").read_bytes(), original)
            self.assertEqual(self.ctx.projects.get_user_projects("alice")[0]["staticPublication"], previous)
        self.assertFalse(list(self.destination.parent.glob(".publish-*")))

    def test_failed_commit_restores_publication(self):
        previous = self.export()
        original = (self.destination / "index.html").read_bytes()
        with (
            patch.object(self.ctx.projects, "update_publication", side_effect=OSError("metadata failed")),
            self.assertRaises(OSError),
        ):
            self.export()
        self.assertEqual((self.destination / "index.html").read_bytes(), original)
        self.assertEqual(self.ctx.projects.get_user_projects("alice")[0]["staticPublication"], previous)

    def test_failed_new_commit_leaves_no_publication(self):
        with (
            patch.object(self.ctx.projects, "update_publication", side_effect=OSError("metadata failed")),
            self.assertRaises(OSError),
        ):
            self.export()
        self.assertFalse(self.destination.exists())

    def test_user_isolation_and_default_account(self):
        self.create_project("bob", "bob-id")
        with self.assertRaises(web.HTTPNotFound):
            self.export("bob")
        self.export()
        publish_folder(self.ctx, "bob", "bob-id", self.config)
        self.assertTrue((self.root / "p/bob/my-project/index.html").exists())
        self.create_project(None)
        result = self.export(None)
        self.assertEqual(result["urlPath"], "/p/default/my-project/")

    def test_refuses_unrelated_destination(self):
        self.destination.mkdir(parents=True)
        (self.destination / "existing").write_text("keep")
        with self.assertRaises(web.HTTPConflict) as error:
            self.export()
        self.assertIn(str(self.destination), error.exception.text)
        self.assertIn("move the existing folder", error.exception.text)
        self.assertEqual((self.destination / "existing").read_text(), "keep")

    def test_source_traversal_and_overlap(self):
        self.ctx.projects.update_publication("project-id", {"publish": "../outside"}, "alice")
        with self.assertRaises(web.HTTPBadRequest):
            self.export()
        self.ctx.projects.update_publication("project-id", {"publish": "dist"}, "alice")
        config = dict(self.config, directory=str(self.source))
        with self.assertRaises(web.HTTPBadRequest):
            self.export(config=config)

    def test_explicit_project_root_and_missing_output(self):
        config_file = self.root / "users/alice/projects/projects.json"
        projects = json.loads(config_file.read_text())
        projects[0].pop("publish")
        config_file.write_text(json.dumps(projects))
        with self.assertRaises(web.HTTPBadRequest):
            self.export()
        self.ctx.projects.update_publication("project-id", {"publish": "missing"}, "alice")
        with self.assertRaises(web.HTTPBadRequest) as error:
            self.export()
        self.assertIn(str(self.source.parent / "missing"), error.exception.text)
        self.assertIn("Build the project first", error.exception.text)
        self.ctx.projects.update_publication("project-id", {"publish": ""}, "alice")
        self.export()
        self.assertTrue((self.destination / "dist/assets/app.js").is_file())

    async def test_filesystem_error_has_destination_and_reason(self):
        with (
            patch("llms.extensions.share_static.publish_folder", side_effect=PermissionError("Permission denied: output")),
            self.assertRaises(web.HTTPInternalServerError) as error,
        ):
            await self.handler(self.request)
        self.assertIn(self.config["directory"], error.exception.text)
        self.assertIn("Permission denied", error.exception.text)

    def test_directory_read_failure_preserves_publication(self):
        self.export()
        original = (self.destination / "index.html").read_bytes()

        def failed_walk(*args, **kwargs):
            kwargs["onerror"](PermissionError("Cannot read directory"))
            return iter(())

        with (
            patch("llms.extensions.share_static.static.os.walk", side_effect=failed_walk),
            self.assertRaises(PermissionError),
        ):
            self.export()
        self.assertEqual((self.destination / "index.html").read_bytes(), original)

    def test_failed_directory_swap_restores_publication(self):
        self.export()
        original = (self.destination / "index.html").read_bytes()
        rename = os.rename

        def fail_install(source, destination):
            if Path(source).name == "output":
                raise OSError("Cannot install output")
            rename(source, destination)

        with patch("llms.extensions.share_static.static.os.rename", side_effect=fail_install), self.assertRaises(OSError):
            self.export()
        self.assertEqual((self.destination / "index.html").read_bytes(), original)

    def test_symlinks_cannot_escape_source_or_destination(self):
        external = self.root / "private.txt"
        external.write_text("private")
        link = self.source / "link.txt"
        link.symlink_to(external)
        with self.assertRaises(web.HTTPBadRequest):
            self.export()
        link.unlink()
        directory = self.source / "outside"
        directory.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(web.HTTPBadRequest):
            self.export()
        directory.unlink()
        (self.root / "p").mkdir(exist_ok=True)
        (self.root / "p/alice").rmdir()
        (self.root / "p/alice").symlink_to(self.root / "users/alice", target_is_directory=True)
        with self.assertRaises(web.HTTPBadRequest):
            self.export()
        self.assertFalse((self.root / "users/alice/my-project").exists())

    async def test_project_edits_preserve_publication_metadata(self):
        result = self.export()
        save = next(c.args[1] for c in self.ctx.add_post.call_args_list if c.args[0] == "save/{name}")
        self.request.match_info = {"name": "My Project"}
        self.request.json = AsyncMock(
            return_value={
                "name": "Renamed",
                "folder": "my-project",
                "publish": "dist",
                "staticPublication": {"urlPath": "/forged/"},
            }
        )
        await save(self.request)
        self.assertEqual(self.ctx.projects.get_user_projects("alice")[0]["staticPublication"], result)
        self.assertEqual(self.export()["urlPath"], result["urlPath"])

    def test_output_settings_changed_during_copy_rolls_back(self):
        from llms.extensions.share_static.static import copy_output

        previous = self.export()

        def changed(source, stage):
            copy_output(source, stage)
            self.ctx.projects.update_publication("project-id", {"publish": ""}, "alice")

        with (
            patch("llms.extensions.share_static.static.copy_output", side_effect=changed),
            self.assertRaises(web.HTTPConflict),
        ):
            self.export()
        self.assertEqual(self.ctx.projects.get_user_projects("alice")[0]["staticPublication"], previous)

    def test_standalone_static_server(self):
        self.export()
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(self.root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/p/alice/my-project/"
            with urlopen(url) as response:
                self.assertIn(b'<base href="/p/alice/my-project/">', response.read())
            with urlopen(url + "assets/app.js") as response:
                self.assertEqual(response.read(), (self.source / "assets/app.js").read_bytes())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class IndexRewriteTests(unittest.TestCase):
    def test_mixed_case_attributes_quotes_and_external_urls(self):
        source = """<!doctype html><HTML><HEAD data-theme="a"><title>Hi</title></HEAD>
        <body><IMG SRC='/images/x.png'><link href=/style.css><a HREF="//cdn.example.com/x">external</a>
        <script src="https://example.com/a.js"></script><a href="#anchor">anchor</a>
        <img data-note='src="/leave"' src="/a.png?x=1&amp;y=2">
        <script>const text = '<img src="/unchanged">';</script><!-- <base href="/fake"> -->
        </body></HTML>"""
        result = rewrite_index(source, "/p/user/project/")
        self.assertIn('<HEAD data-theme="a">\n    <base href="/p/user/project/">', result)
        self.assertIn('SRC="images/x.png"', result)
        self.assertIn('href="style.css"', result)
        self.assertIn('HREF="//cdn.example.com/x"', result)
        self.assertIn('src="https://example.com/a.js"', result)
        self.assertIn('href="#anchor"', result)
        self.assertIn("data-note='src=\"/leave\"'", result)
        self.assertIn('src="a.png?x=1&amp;y=2"', result)
        self.assertIn("const text = '<img src=\"/unchanged\">';", result)
        self.assertIn('<!-- <base href="/fake"> -->', result)
        self.assertEqual(rewrite_index(result, "/p/user/project/"), result)

    def test_existing_base_is_preserved(self):
        source = '<head><BASE href="https://example.com/"></head><img src="/x">'
        self.assertEqual(rewrite_index(source, "/p/u/p/"), source)

    def test_missing_head_and_fragments(self):
        for source in ("<html><body>Hi</body></html>", "<h1>Hi</h1>"):
            result = rewrite_index(source, "/p/u/p/")
            self.assertIn('<head>\n    <base href="/p/u/p/">\n</head>', result)
            self.assertIn("Hi", result)
