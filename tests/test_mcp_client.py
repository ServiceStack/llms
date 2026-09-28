"""MCP parity fixtures: real HTTP, SQLite approvals, principal isolation, and restart."""

import argparse
import asyncio
import copy
import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from llms.extensions.app.db import AppDB
from llms.extensions.mcp_client import install, schema
from llms.extensions.mcp_client.approvals import Approvals
from llms.extensions.mcp_client.client import Client
from llms.extensions.mcp_client.common import McpError, alias, digest, dumps
from llms.extensions.mcp_client.results import map_result
from llms.extensions.mcp_client.transport import HttpSession, NetworkPolicy
from llms.main import AppExtensions, ExtensionContext, ToolApprovalPending

main = importlib.import_module("llms.main")


class HeaderAuth:
    def get_username(self, request):
        return request.headers.get("Test-User") if request is not None else None

    def check_auth(self, request):
        user = self.get_username(request)
        return bool(user), {"userName": user} if user else None

    def get_session(self, request):
        return {"roles": ["Employee"]} if self.get_username(request) else None


class McpFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls, self.methods, self.headers = [], [], []
        self.mode = "json"
        self.oauth_scope_options = None
        self.oauth_challenge_scopes = None
        self.oauth_authorization_scopes = None
        self.version = "2025-06-18"
        self.tools = [
            {
                "name": "echo",
                "description": "Echo text",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "user": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            }
        ]
        self.request_gate = None
        self.sse_close_gate = asyncio.Event()

        async def endpoint(request):
            if request.method == "DELETE":
                return web.Response(status=204)
            if isinstance(self.mode, int):
                return web.Response(status=self.mode, text="echoed-secret")
            body = await request.json()
            if self.mode == "oauth_challenge" and not request.headers.get("Authorization"):
                return web.Response(
                    status=401,
                    headers={
                        "WWW-Authenticate": 'Bearer resource_metadata="'
                        + str(self.remote.make_url("/custom-metadata"))
                        + '"'
                        + (f', scope="{self.oauth_challenge_scopes}"' if self.oauth_challenge_scopes else '')
                    },
                )
            self.methods.append(body["method"])
            self.headers.append(dict(request.headers))
            if "id" not in body:
                return web.Response(status=202)
            method = body["method"]
            if method == "server/discover":
                if self.mode == "github_legacy":
                    return web.json_response({"jsonrpc": "2.0", "id": body["id"],
                                              "error": {"code": -32601, "message": "Method not found"}})
                result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
            elif method == "initialize":
                result = {
                    "protocolVersion": self.version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            elif method == "tools/list":
                result = {"tools": self.tools}
                if self.mode == "cursor_loop":
                    result["tools"] = []
                    result["nextCursor"] = "again"
            elif method == "tools/call":
                self.calls.append(body["params"])
                if self.request_gate:
                    await self.request_gate.wait()
                if self.mode == "lost":
                    request.transport.close()
                    return web.Response()
                if self.mode in ("header_required", "github_legacy") and request.headers.get("Mcp-Param-owner") != body["params"]["arguments"].get("owner"):
                    return web.json_response({"jsonrpc": "2.0", "id": body["id"],
                                              "error": {"code": -32020, "message": "Missing Mcp-Param-owner"}}, status=400)
                if self.mode == "rejected":
                    return web.json_response({"jsonrpc": "2.0", "id": body["id"],
                                              "error": {"code": -32602, "message": "Invalid arguments"}})
                if self.mode == "http_rejected":
                    return web.Response(status=400)
                result = {
                    "content": [{"type": "text", "text": "done"}],
                    "structuredContent": body["params"]["arguments"],
                }
                if self.mode in ("input_required", "sse_input_required"):
                    result = {"resultType": "input_required", "requestState": "do-not-replay"}
            else:
                return web.json_response({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601}})
            payload = {"jsonrpc": "2.0", "id": body["id"], "result": result}
            headers = {"MCP-Session-Id": "fixture-session"} if method == "initialize" else {}
            if self.mode == "sse_open" and method == "tools/call":
                stream = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
                await stream.prepare(request)
                await stream.write(("data: " + dumps(payload) + "\n\n").encode())
                await self.sse_close_gate.wait()
                return stream
            if self.mode.startswith("sse"):
                return web.Response(
                    text=":comment\r\ndata: " + dumps(payload) + "\r\n\r\n",
                    content_type="text/event-stream",
                    headers=headers,
                )
            return web.json_response(payload, headers=headers)

        remote = web.Application()
        remote.router.add_route("*", "/mcp", endpoint)
        self.oauth_requests = []

        async def resource_metadata(request):
            return web.json_response(
                {
                    "resource": str(self.remote.make_url("/mcp")),
                    "authorization_servers": [str(self.remote.make_url("/issuer"))],
                    **({"scopes_supported": self.oauth_scope_options} if self.oauth_scope_options is not None else {}),
                }
            )

        async def issuer_metadata(request):
            return web.json_response(
                {
                    "issuer": str(self.remote.make_url("/issuer")),
                    "authorization_endpoint": str(self.remote.make_url("/authorize")),
                    "token_endpoint": str(self.remote.make_url("/token")),
                    "code_challenge_methods_supported": ["S256"],
                    "authorization_response_iss_parameter_supported": True,
                    **({"scopes_supported": self.oauth_authorization_scopes}
                       if self.oauth_authorization_scopes is not None else {}),
                }
            )

        async def token_endpoint(request):
            data = dict(await request.post())
            self.oauth_requests.append(data)
            if self.mode == "oauth_token_error":
                return web.json_response({"error": "bad_verification_code", "error_description": "do not display this"})
            if "application/json" not in request.headers.get("Accept", ""):
                return web.Response(text="access_token=invalid&token_type=Bearer", content_type="application/x-www-form-urlencoded")
            return web.json_response(
                {
                    "access_token": "private-access-" + str(len(self.oauth_requests)),
                    "refresh_token": "private-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )

        remote.router.add_get("/.well-known/oauth-protected-resource/mcp", resource_metadata)
        remote.router.add_get("/custom-metadata", resource_metadata)
        remote.router.add_get("/.well-known/oauth-authorization-server/issuer", issuer_metadata)
        remote.router.add_post("/token", token_endpoint)
        self.remote = TestServer(remote)
        await self.remote.start_server()
        self.app = AppExtensions(argparse.Namespace(), {})
        self.app.get_user_path = lambda user=None: os.path.join(self.temp.name, "user", user or "default")
        self.app.auth_provider = HeaderAuth()
        self.app.should_cancel_thread = lambda context: False
        self.config = {
            "enabled": True,
            "servers": [
                {
                    "id": "work",
                    "endpoint": str(self.remote.make_url("/mcp")),
                    "allowedTools": ["echo"],
                    "allowedUsers": ["alice"],
                    "requiredRoles": ["Employee"],
                }
            ],
            "networkPolicy": {
                "allowHttp": True,
                "allowedPorts": [self.remote.port],
                "allowedPrivateNetworks": ["127.0.0.0/8"],
            },
        }
        self.app.config = {}
        self.app.mcp_client_config = self.config
        self.ctx = ExtensionContext(self.app, str(Path(__file__).resolve().parents[1] / "llms/extensions/mcp_client"))
        install(self.ctx)
        self.client = self.app.mcp_client
        self.db = AppDB(self.ctx, os.path.join(self.temp.name, "app.sqlite"))
        self.app.agent_db = self.db
        self.app.db = self.db
        self.app.agent_requests = {}
        self.app.notify_thread_update = lambda _: None
        self.thread = await self.db.create_thread_async(
            {"messages": [{"role": "user", "content": "hello"}], "model": "fixture"}, user="alice"
        )
        self.run = self.db.create_agent_run(self.thread, "alice", "fixture")
        self.db.update_agent_run(self.run, {"status": "running"})
        self.context = {
            "request": SimpleNamespace(headers={"Test-User": "alice", "X-Mcp-Client": "1"}),
            "user": "alice",
            "owner": "alice",
            "threadId": self.thread,
            "runId": self.run,
        }
        self.old_app = main.g_app
        main.g_app = self.app

        async def persist(chat, context):
            await self.db.update_thread_async(self.thread, {"messages": chat["messages"]}, user="alice")

        self.app.chat_tool_filters.append(persist)
        api = web.Application()
        for method in ("get", "post", "delete"):
            for path, handler, kwargs in getattr(self.app, "server_add_" + method):
                getattr(api.router, "add_" + method)(path, handler, **kwargs)
        self.http = TestClient(TestServer(api))
        await self.http.start_server()

    async def asyncTearDown(self):
        self.sse_close_gate.set()
        if self.request_gate:
            self.request_gate.set()
        await self.http.close()
        await self.client.close()
        await self.remote.close()
        self.db.close()
        main.g_app = self.old_app
        self.temp.cleanup()

    async def resolve(self):
        handles = await self.client.resolve(self.context, "mcp_work")
        self.context["contextualTools"] = handles
        return next(iter(handles.values()))

    async def batch(self, extra=None):
        handle = await self.resolve()
        call = {
            "id": "call-1",
            "type": "function",
            "function": {"name": handle["tool"]["name"], "arguments": '{"text":"original","user":"remote-user"}'},
        }
        message = {"role": "assistant", "tool_calls": [call] + (extra or [])}
        messages = [{"role": "user", "content": "hello"}, message]
        await self.db.update_thread_async(self.thread, {"messages": messages}, user="alice")
        ident = await self.client.approvals.prepare(message, self.context)
        return ident, message

    async def test_delete_user_credentials_removes_only_that_users_mcp_state(self):
        server = self.client.servers[0]
        self.client.store.connect("alice", server["id"])
        self.client.store.connect("bob", server["id"])
        self.client.entry("alice", server)["state"] = "connected"
        self.client.entry("bob", server)["state"] = "connected"

        self.client.delete_user_credentials("alice")

        self.assertEqual(self.client.store.binding("alice", server["id"])["revision"], 0)
        self.assertGreater(self.client.store.binding("bob", server["id"])["revision"], 0)
        self.assertEqual(self.client.entry("alice", server)["state"], "disconnected")
        self.assertEqual(self.client.entry("bob", server)["state"], "connected")

    async def test_single_user_can_add_connection_from_tools_ui(self):
        self.app.auth_provider = None
        self.client.config["singleUserMode"] = True
        initial = await self.http.get("/ext/mcp_client/config.json")
        self.assertEqual(initial.status, 200)
        config = await initial.json()
        self.assertTrue(config["canEdit"])
        body = {"revision": config["revision"], "servers": [
            {"id": "solo", "endpoint": str(self.remote.make_url("/mcp")), "auth": {"mode": "bearer"},
             "allowedTools": ["*"]}
        ]}
        denied = await self.http.post("/ext/mcp_client/config.json", json=body)
        self.assertEqual(denied.status, 403)
        saved = await self.http.post("/ext/mcp_client/config.json", headers={"X-Mcp-Client": "1"}, json=body)
        self.assertEqual(saved.status, 200, await saved.text())
        token = await self.http.post("/ext/mcp_client/connections/solo/credentials",
                                     headers={"X-Mcp-Client": "1"}, json={"token": "github-pat"})
        self.assertEqual(token.status, 200, await token.text())
        self.assertEqual(json.loads(self.client.store.binding("default", "solo")["credential"])["accessToken"], "github-pat")
        await self.client.catalog(self.client.server("solo", "default"),
                                  {"request": SimpleNamespace(headers={"X-Mcp-Client": "1"}),
                                   "user": None, "owner": "default"}, refresh=True)
        self.assertTrue(any(headers.get("Authorization") == "Bearer github-pat" for headers in self.headers))
        connections = await self.http.get("/ext/mcp_client/connections")
        self.assertEqual([(row["id"], row["scope"]) for row in await connections.json()], [("solo", "personal")])

    async def test_oauth_issuer_discovery_from_protected_resource(self):
        endpoint = str(self.remote.make_url("/mcp"))
        response = await self.http.post(
            "/ext/mcp_client/oauth/issuers",
            json={"endpoint": endpoint},
            headers={"Test-User": "alice", "X-Mcp-Client": "1"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["issuers"], [str(self.remote.make_url("/issuer"))])
        self.mode = "oauth_challenge"
        response = await self.http.post(
            "/ext/mcp_client/oauth/issuers",
            json={"endpoint": endpoint},
            headers={"Test-User": "alice", "X-Mcp-Client": "1"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["issuers"], [str(self.remote.make_url("/issuer"))])

    async def test_oauth_scope_selection_matches_csharp_precedence(self):
        server = {
            "endpoint": str(self.remote.make_url("/mcp")),
            "oauthIssuer": str(self.remote.make_url("/issuer")),
            "oauthScopes": ["configured.read"],
        }
        _, scopes = await self.client.oauth.metadata(server)
        self.assertEqual(scopes, ["configured.read"])
        self.oauth_scope_options = ["resource.read", "resource.write"]
        _, scopes = await self.client.oauth.metadata(server)
        self.assertEqual(scopes, ["resource.read", "resource.write"])
        self.mode = "oauth_challenge"
        self.oauth_challenge_scopes = "challenge.read"
        _, scopes = await self.client.oauth.metadata(server)
        self.assertEqual(scopes, ["challenge.read"])
        self.oauth_authorization_scopes = ["offline_access"]
        _, scopes = await self.client.oauth.metadata(server)
        self.assertEqual(scopes, ["challenge.read", "offline_access"])

    async def test_local_single_user_oauth_uses_default_callback_and_completes(self):
        from urllib.parse import parse_qs, urlsplit

        self.app.auth_provider = None
        self.client.config["singleUserMode"] = True
        initial = await self.http.get("/ext/mcp_client/config.json")
        self.assertEqual(initial.status, 200)
        config = await initial.json()
        callback = str(self.http.make_url("/ext/mcp_client/oauth/callback"))
        self.assertEqual(config["oauthRedirectUri"], callback)
        server = {
            "id": "solo_oauth", "endpoint": str(self.remote.make_url("/mcp")),
            "auth": {"mode": "user_oauth"}, "allowedTools": ["*"],
            "oauthClientId": "fixture-client", "oauthIssuer": str(self.remote.make_url("/issuer")),
        }
        saved = await self.http.post("/ext/mcp_client/config.json", headers={"X-Mcp-Client": "1"},
                                     json={"revision": config["revision"], "servers": [server]})
        self.assertEqual(saved.status, 200, await saved.text())
        self.assertEqual((await saved.json())["oauthRedirectUri"], callback)
        self.oauth_scope_options = ["repo", "read:org"]
        self.oauth_authorization_scopes = ["offline_access"]
        connect = await self.http.post("/ext/mcp_client/connections/solo_oauth/connect",
                                       headers={"X-Mcp-Client": "1"}, json={})
        self.assertEqual(connect.status, 200, await connect.text())
        authorize = (await connect.json())["authorizationUrl"]
        params = parse_qs(urlsplit(authorize).query)
        self.assertEqual(params["redirect_uri"], [callback])
        self.assertEqual(params["scope"], ["repo read:org offline_access"])
        completed = await self.http.get("/ext/mcp_client/oauth/callback", params={
            "code": "fixture-code", "state": params["state"][0], "iss": server["oauthIssuer"],
        })
        self.assertEqual(completed.status, 200, await completed.text())
        self.assertIn("Connection authorized", await completed.text())
        self.assertEqual(self.client.status("default", self.client.server("solo_oauth", "default"))["state"], "ready")
        # Reauthorizing an existing connection uses freshly advertised scopes.
        self.oauth_scope_options = ["repo", "read:org", "read:user"]
        reconnect = await self.http.post("/ext/mcp_client/connections/solo_oauth/connect",
                                         headers={"X-Mcp-Client": "1"}, json={})
        self.assertEqual(reconnect.status, 200, await reconnect.text())
        params = parse_qs(urlsplit((await reconnect.json())["authorizationUrl"]).query)
        self.assertEqual(params["scope"], ["repo read:org read:user offline_access"])
        self.mode = "oauth_token_error"
        failed = await self.http.get("/ext/mcp_client/oauth/callback", params={
            "code": "fixture-code", "state": params["state"][0], "iss": server["oauthIssuer"],
        })
        self.assertEqual(failed.status, 400)
        page = await failed.text()
        self.assertIn("oauth_bad_verification_code", page)
        self.assertNotIn("do not display this", page)
        self.assertIn("type: 'failed'", page)
        self.mode = 401
        reconnect = await self.http.post("/ext/mcp_client/connections/solo_oauth/connect",
                                         headers={"X-Mcp-Client": "1"}, json={})
        self.assertEqual(reconnect.status, 200, await reconnect.text())
        params = parse_qs(urlsplit((await reconnect.json())["authorizationUrl"]).query)
        failed = await self.http.get("/ext/mcp_client/oauth/callback", params={
            "code": "fixture-code", "state": params["state"][0], "iss": server["oauthIssuer"],
        })
        self.assertEqual(failed.status, 400)
        self.assertIn("Signed in, but tools could not load", await failed.text())
        self.assertIsNotNone(self.client.store.binding("default", "solo_oauth")["credential"])

    async def test_oauth_callback_prefers_configured_and_github_auth_origins(self):
        from types import SimpleNamespace

        request = SimpleNamespace(scheme="http", host="127.0.0.1:8000")
        self.assertEqual(self.client.oauth_redirect_uri(request),
                         "http://127.0.0.1:8000/ext/mcp_client/oauth/callback")
        self.app.auth_provider.redirect_uri = "https://chat.example.com/auth/github/callback"
        self.assertEqual(self.client.oauth_redirect_uri(request),
                         "https://chat.example.com/ext/mcp_client/oauth/callback")
        self.client.config["oauthRedirectUri"] = "https://custom.example.com/ext/mcp_client/oauth/callback"
        self.assertEqual(self.client.oauth_redirect_uri(request),
                         "https://custom.example.com/ext/mcp_client/oauth/callback")

    async def test_connect_all_skips_disabled_bindings_and_reuses_enabled_connections(self):
        headers = {"Test-User": "alice", "X-Mcp-Client": "1"}
        server = self.client.servers[0]
        self.client.store.connect("alice", server["id"], disconnected=True)
        response = await self.http.post("/ext/mcp_client/connections/connect-all", headers=headers, json={})
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), [])
        self.assertEqual(self.methods, [])
        self.assertTrue(self.client.status("alice", server)["disabled"])
        self.client.store.connect("alice", server["id"])
        response = await self.http.post("/ext/mcp_client/connections/connect-all", headers=headers, json={})
        self.assertTrue((await response.json())[0]["connected"])
        self.assertIn("tools/list", self.methods)
        self.assertFalse(self.client.status("alice", server)["disabled"])
        response = await self.http.post("/ext/mcp_client/connections/connect-all", headers={"Test-User": "alice"}, json={})
        self.assertNotEqual(response.status, 200)

    async def test_http_connection_errors_are_actionable_and_do_not_echo_response_bodies(self):
        server = self.client.servers[0]
        for status, code in ((400, "http_400"), (401, "auth_required"), (403, "access_denied"),
                             (404, "http_404"), (429, "rate_limited"), (503, "http_503")):
            self.mode = status
            self.client.entry(self.context["owner"], server)["retryAfter"] = 0
            with self.assertRaises(McpError) as caught:
                await self.client.catalog(server, self.context, refresh=True)
            self.assertEqual(caught.exception.code, code)
            self.assertIn(f"HTTP {status}", str(caught.exception))
            self.assertNotIn("echoed-secret", str(caught.exception))

    async def test_personal_configuration_routes_and_owner_isolation(self):
        headers = {"Test-User": "alice", "X-Mcp-Client": "1"}
        response = await self.http.get("/ext/mcp_client/config.json", headers=headers)
        original = await response.json()
        server = {"id": "mine", "endpoint": str(self.remote.make_url("/mcp")), "allowedTools": ["echo"]}
        body = {"revision": original["revision"], "servers": [server]}
        response = await self.http.post("/ext/mcp_client/config.json", headers=headers, json=body)
        self.assertEqual(response.status, 200, await response.text())
        saved = await response.json()
        self.assertTrue(self.client.configuration_path("alice").exists())
        self.assertEqual([s["id"] for s in self.client.get_servers("bob")], ["work"])
        old = self.client.server("mine", "alice")
        with self.assertRaises(McpError):
            await self.client.access(old, {**self.context, "user": "bob", "request": SimpleNamespace(headers={"Test-User": "bob"})}, "invoke")
        response = await self.http.post("/ext/mcp_client/config.json", headers=headers, json=body)
        self.assertEqual(response.status, 409)
        for invalid in [dict(server, id="work"), dict(server, approval="never"),
                        dict(server, auth={"mode": "host_secret", "secretReference": "SECRET"}),
                        dict(server, endpoint=None), dict(server, allowedUsers=["bob"])]:
            response = await self.http.post("/ext/mcp_client/config.json", headers=headers,
                                           json={"revision": saved["revision"], "servers": [invalid]})
            self.assertEqual(response.status, 400)
        response = await self.http.post("/ext/mcp_client/config.json", headers={"Test-User": "alice"}, json=body)
        self.assertEqual(response.status, 403)
        response = await self.http.post("/ext/mcp_client/config.json", headers={**headers, "Test-User": "default"}, json=body)
        self.assertEqual(response.status, 403)
        response = await self.http.post("/ext/mcp_client/config.json", headers=headers,
                                       json={"revision": saved["revision"], "servers": []})
        self.assertEqual(response.status, 200)
        with self.assertRaises(McpError):
            await self.client.access(old, self.context, "invoke")

    async def test_shared_configuration_file_and_personal_scope_are_distinct(self):
        path = self.client.configuration_path("default")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps({"servers": [{"id": "shared", "endpoint": str(self.remote.make_url("/mcp")),
                                           "allowedUsers": ["alice"], "allowedTools": ["echo"]}]}))
        self.assertEqual([s["id"] for s in self.client.get_servers("alice")], ["work", "shared"])
        response = await self.http.get("/ext/mcp_client/connections", headers={"Test-User": "alice"})
        rows = await response.json()
        self.assertTrue(all(s["scope"] == "shared" for s in rows))
        config = self.client.personal_configuration("alice")
        self.assertEqual(config["servers"], [])
        with self.assertRaises(McpError):
            self.client.personal_configuration("../bob")

    async def test_configuration_edits_invalidate_credentials_callbacks_and_handles(self):
        server = {"id": "mine", "endpoint": str(self.remote.make_url("/mcp")), "allowedTools": ["echo"]}
        config = self.client.personal_configuration("alice")
        saved = self.client.save_configuration("alice", {"revision": config["revision"], "servers": [server]})
        old = self.client.server("mine", "alice")
        self.client.store.connect("alice", "mine")
        binding = self.client.store.binding("alice", "mine")
        self.client.store.credential("alice", "mine", binding["revision"], "opaque-encrypted-token")
        self.client.store.oauth_start("pending-config-edit", "alice", "mine", binding["revision"], "opaque-state")
        edited = {**server, "displayName": "Personal café"}
        saved = self.client.save_configuration("alice", {"revision": saved["revision"], "servers": [edited]})
        self.assertEqual(saved["servers"][0]["displayName"], "Personal café")
        binding = self.client.store.binding("alice", "mine")
        self.assertIsNone(binding["credential"])
        self.assertTrue(binding["disconnected"])
        with self.assertRaises(McpError):
            self.client.store.oauth_claim("pending-config-edit", "alice")
        with self.assertRaises(McpError):
            await self.client.access(old, self.context, "invoke")
        self.client.save_configuration("alice", {"revision": saved["revision"], "servers": []})
        with self.assertRaises(McpError):
            self.client.server("mine", "alice")

    async def test_shared_file_audience_matches_csharp_and_reloads(self):
        path = self.client.configuration_path("default")
        path.parent.mkdir(parents=True, exist_ok=True)
        server = {"id": "shared", "endpoint": str(self.remote.make_url("/mcp")), "allowedUsers": []}
        path.write_text(dumps({"servers": [server]}), encoding="utf-8-sig")
        await self.client.access(self.client.server("shared", "alice"), self.context, "discover")
        server["allowedUsers"] = ["*"]
        path.write_text(dumps({"servers": [server]}), encoding="utf-8")
        with self.assertRaises(McpError):
            await self.client.access(self.client.server("shared", "alice"), self.context, "discover")
        server["allowedUsers"] = ["alice"]
        path.write_text(dumps({"servers": [server]}), encoding="utf-8")
        await self.client.access(self.client.server("shared", "alice"), self.context, "discover")

    async def test_invalid_configuration_is_rejected_before_save(self):
        server = {"id": "mine", "endpoint": str(self.remote.make_url("/mcp"))}
        config = self.client.personal_configuration("alice")
        for invalid in [dict(server, oauthIssuer="http://untrusted.example/issuer"),
                        dict(server, includeInAll=float("nan")), dict(server, allowedTools=[None]),
                        dict(server, auth={"mode": "anonymous", "secretReference": None})]:
            with self.subTest(server=invalid), self.assertRaises(McpError):
                self.client.save_configuration("alice", {"revision": config["revision"], "servers": [invalid]})
        self.assertEqual(self.client.personal_configuration("alice"), config)
        path = self.client.configuration_path("alice")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff")
        with self.assertRaises(McpError):
            self.client.personal_configuration("alice")

    async def test_bearer_token_defaults_to_plaintext_storage(self):
        server = self.client.servers[0]
        server["auth"] = {"mode": "bearer"}
        headers = {"Test-User": "alice", "X-Mcp-Client": "1"}
        response = await self.http.post(
            "/ext/mcp_client/connections/work/credentials",
            headers=headers, json={"token": "fixture-pat"},
        )
        self.assertEqual(response.status, 200)
        self.assertNotIn("fixture-pat", await response.text())
        self.assertEqual(json.loads(self.client.store.binding("alice", "work")["credential"])["accessToken"], "fixture-pat")
        await self.client.catalog(server, self.context, refresh=True)
        self.assertTrue(any(h.get("Authorization") == "Bearer fixture-pat" for h in self.headers))

    async def test_bearer_token_route_with_host_protection_sends_authorization(self):
        vault = {}
        def protect(value, purpose):
            key = 'opaque-' + str(len(vault))
            vault[key] = (purpose, value)
            return key
        def unprotect(value, purpose):
            saved_purpose, plain = vault[value]
            self.assertEqual(saved_purpose, purpose)
            return plain
        self.client.hooks.update(protect=protect, unprotect=unprotect)
        server = self.client.servers[0]
        server['auth'] = {'mode': 'bearer'}
        headers = {'Test-User': 'alice', 'X-Mcp-Client': '1'}
        response = await self.http.post('/ext/mcp_client/connections/work/credentials', headers=headers, json={'token': 'fixture-pat'})
        self.assertEqual(response.status, 200)
        self.assertNotIn('fixture-pat', await response.text())
        self.assertNotIn('fixture-pat', self.client.store.binding('alice', 'work')['credential'])
        with self.assertRaises(McpError):
            self.client.bearer_credential(server, 'bob')
        await self.client.catalog(server, self.context, refresh=True)
        self.assertTrue(any(h.get('Authorization') == 'Bearer fixture-pat' for h in self.headers))
        old = self.client.store.binding('alice', 'work')['revision']
        self.client.save_bearer(server, 'alice', 'replacement-pat')
        self.assertGreater(self.client.store.binding('alice', 'work')['revision'], old)
        response = await self.http.post('/ext/mcp_client/connections/work/credentials', headers={**headers, 'Test-User':'bob'}, json={'token':'bob-pat'})
        self.assertEqual(response.status, 403)
        await self.http.delete('/ext/mcp_client/connections/work/credentials', headers=headers)
        with self.assertRaises(McpError):
            self.client.bearer_credential(server, 'alice')

    async def test_http_and_sse_and_per_request_generation(self):
        for mode, version in [
            ("json", "2025-06-18"),
            ("sse", "2025-06-18"),
            ("json", "2026-07-28"),
            ("sse", "2026-07-28"),
        ]:
            with self.subTest(mode=mode, version=version):
                self.mode = mode
                server = {**self.client.servers[0], "protocolVersion": version}
                async with HttpSession(server, self.client.policy, self.client.limits) as session:
                    self.assertEqual(session.protocol, version)
                    self.assertEqual(len((await session.list_tools())["tools"]), 1)
                    self.assertEqual((await session.call("echo", {"text": "x"}))["structuredContent"], {"text": "x"})
                    with self.assertRaisesRegex(McpError, "replay"):
                        await session.call("echo", {"text": "x"})
        self.assertEqual(len(self.calls), 4)
        self.assertIn("server/discover", self.methods)
        self.assertTrue(any(h.get("MCP-Session-Id") == "fixture-session" for h in self.headers))

    async def test_sse_tool_result_does_not_wait_for_stream_close(self):
        self.mode = "sse_open"
        server = {**self.client.servers[0], "protocolVersion": "2025-06-18"}
        async with HttpSession(server, self.client.policy, self.client.limits) as session:
            result = await asyncio.wait_for(session.call("echo", {"text": "x"}), 1)
        self.assertEqual(result["structuredContent"], {"text": "x"})
        self.assertFalse(self.sse_close_gate.is_set())

    async def test_catalog_isolation_allowlist_alias_and_default_all(self):
        handle = await self.resolve()
        self.assertEqual(handle["tool"]["name"], alias("work", "echo"))
        self.assertFalse(self.app.tools)
        self.assertEqual(await self.client.resolve(self.context, "all"), {})
        self.assertEqual(await self.client.resolve(self.context, "none"), {})
        bob = {"request": SimpleNamespace(headers={"Test-User": "bob"}), "user": "bob"}
        self.assertEqual(await self.client.resolve(bob, "__list"), {})
        fake = {**self.context, "user": "bob"}
        with self.assertRaises(McpError):
            await self.client.invoke(handle, {"text": "x"}, fake, approved=True)
        self.assertEqual(self.calls, [])

    async def test_routes_and_direct_execution_require_approval(self):
        headers = {"Test-User": "alice", "X-Mcp-Client": "1"}
        response = await self.http.get("/ext/mcp_client/connections", headers=headers)
        connections = await response.json()
        self.assertEqual(connections[0]["group"], "mcp_work")
        response = await self.http.get("/ext/mcp_client/connections/work/tools", headers=headers)
        tools = await response.json()
        self.assertEqual(set(tools[0]), {"name", "remoteName", "description", "schema", "requiresApproval", "alwaysApproved", "group"})
        handle = await self.resolve()
        with self.assertRaisesRegex(McpError, "approval"):
            await self.client.invoke(handle, {"text": "x"}, self.context)
        response = await self.http.post("/ext/mcp_client/connections/work/disconnect", headers={"Test-User": "alice"})
        self.assertEqual(response.status, 403)
        response = await self.http.post("/ext/mcp_client/connections/work/disconnect", headers=headers)
        self.assertEqual(await response.json(), {"state": "disconnected"})
        with self.assertRaises(McpError):
            await self.client.invoke(handle, {"text": "x"}, self.context, approved=True)
        self.assertEqual(self.calls, [])

    async def test_edited_approval_duplicate_approve_and_resume_preserve_calls(self):
        ident, message = await self.batch()
        with self.assertRaises(ToolApprovalPending):
            await self.client.approvals.execute(ident, self.context)
        row = self.client.store.rows("alice", batch=ident)[0]
        edited = {"text": "edited", "user": "real-remote-parameter"}
        headers = {"Test-User": "alice", "X-Mcp-Client": "1"}
        url = f"/ext/mcp_client/approvals/{row['id']}/approve"
        responses = await asyncio.gather(
            *[self.http.post(url, json={"args": edited}, headers=headers) for _ in range(2)]
        )
        self.assertTrue(all(r.status == 200 for r in responses), [await r.text() for r in responses])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["arguments"], edited)
        row = self.client.store.row(row["id"], "alice")
        self.assertEqual(row["effectiveArgs"], edited)
        self.tools[0]["inputSchema"] = {"type": "object", "properties": {"different": {"type": "string"}}}
        repeated = await self.http.post(url, json={"args": edited}, headers=headers)
        self.assertEqual(repeated.status, 200)
        self.assertEqual(len(self.calls), 1)
        chat = {"messages": [{"role": "user", "content": "hello"}, message]}
        await self.client.approvals.resume(chat, self.context)
        self.assertEqual(chat["messages"][-1]["tool_call_id"], "call-1")
        self.assertIn("edited", chat["messages"][-1]["content"])
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"])["text"], "original")
        await self.client.approvals.resume(chat, self.context)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(sum(m["role"] == "tool" for m in chat["messages"]), 1)

    async def test_always_approve_is_owner_tool_schema_scoped_and_revocable(self):
        ident, _ = await self.batch()
        row = self.client.store.rows("alice", batch=ident)[0]
        headers = {"Test-User": "alice", "X-Mcp-Client": "1"}
        response = await self.http.post(
            f"/ext/mcp_client/approvals/{row['id']}/approve",
            json={"args": {"text": "once"}, "alwaysApprove": True}, headers=headers,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.calls), 1)
        grants = await self.http.get("/ext/mcp_client/connections/work/approval-grants", headers=headers)
        self.assertEqual([x["tool"] for x in await grants.json()], ["echo"])
        tools = await self.http.get("/ext/mcp_client/connections/work/tools", headers=headers)
        self.assertTrue((await tools.json())[0]["alwaysApproved"])
        handle = await self.resolve()
        await self.client.invoke(handle, {"text": "again"}, self.context)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.client.needs_approval(handle["server"], handle["tool"], "alice"))
        self.assertTrue(self.client.needs_approval(handle["server"], handle["tool"], "bob"))
        changed = {**handle["tool"], "schemaHash": "changed"}
        self.assertTrue(self.client.needs_approval(handle["server"], changed, "alice"))
        revoked = await self.http.post("/ext/mcp_client/connections/work/approval-grants/revoke",
                                       json={"tool": "echo"}, headers=headers)
        self.assertEqual(revoked.status, 200)
        tools = await self.http.get("/ext/mcp_client/connections/work/tools", headers=headers)
        self.assertFalse((await tools.json())[0]["alwaysApproved"])
        with self.assertRaisesRegex(McpError, "approval"):
            await self.client.invoke(handle, {"text": "after revoke"}, self.context)

    async def test_cross_user_approval_and_schema_validation(self):
        ident, _ = await self.batch()
        row = self.client.store.rows("alice", batch=ident)[0]
        response = await self.http.post(
            f"/ext/mcp_client/approvals/{row['id']}/approve",
            json={"args": {"text": "x"}},
            headers={"Test-User": "bob", "X-Mcp-Client": "1"},
        )
        self.assertEqual(response.status, 404)
        response = await self.http.get(f"/ext/mcp_client/approvals/{self.thread}", headers={"Test-User": "bob"})
        self.assertEqual(response.status, 404)
        with self.assertRaises(McpError):
            await self.client.approvals.decide(row["id"], "approve", {"args": {"text": 123}}, self.context)
        self.assertEqual(self.client.store.row(row["id"], "alice")["status"], "pending")
        self.assertFalse(self.calls)

    async def test_changed_schema_and_credentials_invalidate_approval(self):
        ident, _ = await self.batch()
        row = self.client.store.rows("alice", batch=ident)[0]
        self.tools[0]["inputSchema"]["required"].append("user")
        with self.assertRaisesRegex(McpError, "schema changed"):
            await self.client.approvals.decide(row["id"], "approve", {"args": {"text": "x"}}, self.context)
        self.assertFalse(self.calls)

    async def test_unknown_outcome_is_not_retried_after_restart(self):
        ident, message = await self.batch()
        row = self.client.store.rows("alice", batch=ident)[0]
        self.mode = "lost"
        result = await self.client.approvals.decide(row["id"], "approve", {}, self.context)
        self.assertEqual(result["status"], "outcome_unknown")
        self.assertEqual(len(self.calls), 1)
        # Reconstruct client and SQLite state as after process restart.
        restarted = Client(self.ctx, self.config)
        restarted.approvals = Approvals(restarted)
        try:
            with self.assertRaises(ToolApprovalPending):
                await restarted.approvals.execute(ident, self.context)
            await restarted.approvals.decide(
                row["id"], "reconcile", {"decision": "continue_without_replay"}, self.context
            )
            chat = {"messages": [{"role": "user", "content": "hello"}, message]}
            await restarted.approvals.resume(chat, self.context)
            self.assertEqual(len(self.calls), 1)
            self.assertIn("outcome_unknown", chat["messages"][-1]["content"])
        finally:
            await restarted.close()

    async def test_annotated_arguments_are_mirrored_for_modern_and_legacy_github_sessions(self):
        self.tools[0]["inputSchema"]["properties"]["owner"] = {"type": "string", "x-mcp-header": "owner"}
        self.mode = "header_required"
        for version in ("2026-07-28", "2025-06-18", "auto"):
            with self.subTest(version=version):
                self.mode = "github_legacy" if version == "auto" else "header_required"
                self.version = "2025-11-25" if version == "auto" else "2025-06-18"
                self.client.servers[0]["protocolVersion"] = version
                self.client.invalidate("alice", "work")
                handle = await self.resolve()
                result = await self.client.invoke(
                    handle, {"text": "x", "owner": "ServiceStack"}, self.context, approved=True,
                    invocation_id="header-" + version,
                )
                self.assertFalse(result["isError"])
                self.assertEqual(self.headers[-1].get("Mcp-Param-owner"), "ServiceStack")
        self.assertEqual(len(self.calls), 3)

    async def test_explicit_rpc_rejection_is_a_failed_call_not_uncertain(self):
        handle = await self.resolve()
        self.mode = "rejected"
        with self.assertRaises(McpError) as result:
            await self.client.invoke(handle, {"text": "x"}, self.context, approved=True,
                                     invocation_id="rejected-call")
        self.assertEqual(result.exception.code, "remote_error")
        self.assertEqual(self.client.entry("alice", self.client.servers[0])["state"], "ready")
        with self.client.store.connection() as db:
            invocation = db.execute("SELECT status FROM mcp_invocation WHERE id='rejected-call'").fetchone()
        self.assertEqual(invocation["status"], "failed")

    async def test_http_rejection_is_a_failed_call_not_uncertain(self):
        handle = await self.resolve()
        self.mode = "http_rejected"
        with self.assertRaises(McpError) as result:
            await self.client.invoke(handle, {"text": "x"}, self.context, approved=True,
                                     invocation_id="http-rejected-call")
        self.assertEqual(result.exception.code, "http_400")
        with self.client.store.connection() as db:
            invocation = db.execute("SELECT status FROM mcp_invocation WHERE id='http-rejected-call'").fetchone()
        self.assertEqual(invocation["status"], "failed")

    async def test_unsupported_continuation_does_not_replay(self):
        handle = await self.resolve()
        for mode in ("input_required", "sse_input_required"):
            self.mode = mode
            self.client.invalidate("alice", "work")
            with self.assertRaisesRegex(McpError, "uncertain"):
                await self.client.invoke(handle, {"text": "x"}, self.context, approved=True)
        self.assertEqual(len(self.calls), 2)

    async def test_catalog_cursor_and_duplicate_name_limits(self):
        self.mode = "cursor_loop"
        with self.assertRaises(McpError):
            await self.client.catalog(self.client.servers[0], self.context, refresh=True)
        self.client.invalidate("alice", "work")
        self.mode = "json"
        self.tools *= 2
        with self.assertRaises(McpError):
            await self.client.catalog(self.client.servers[0], self.context, refresh=True)

    async def test_invocation_journal_caches_result_and_never_replays_claim(self):
        handle = await self.resolve()
        first = await self.client.invoke(handle, {"text": "x"}, self.context, approved=True, invocation_id="fixed")
        second = await self.client.invoke(handle, {"text": "x"}, self.context, approved=True, invocation_id="fixed")
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.client.store.invocation_claim("interrupted", "alice")
        with self.assertRaisesRegex(McpError, "already claimed"):
            await self.client.invoke(handle, {"text": "x"}, self.context, approved=True, invocation_id="interrupted")
        self.assertEqual(len(self.calls), 1)

    async def test_background_identity_and_google_constraints_fail_closed(self):
        handle = await self.resolve()
        no_request = {k: v for k, v in self.context.items() if k != "request"}
        with self.assertRaisesRegex(McpError, "authorization"):
            await self.client.invoke(handle, {"text": "x"}, no_request, approved=True)
        with self.assertRaises(McpError):
            self.client.validate_model(self.context, SimpleNamespace(sdk="@ai-sdk/google"))
        self.assertFalse(self.calls)

    async def test_host_secret_requires_audience_and_rotation_invalidates_handle(self):
        config = copy.deepcopy(self.config)
        config["servers"][0]["auth"] = {"mode": "host_secret", "secretReference": "fixture-key"}
        credentials = {"accessToken": "token-1", "revision": "1"}
        client = Client(self.ctx, config, {"credentialStore": lambda _: credentials})
        try:
            handle = next(iter((await client.resolve(self.context, "mcp_work")).values()))
            credentials.update(accessToken="token-2", revision="2")
            with self.assertRaises(McpError):
                await client.invoke(handle, {"text": "x"}, self.context, approved=True)
            self.assertTrue(any(h.get("Authorization") == "Bearer token-1" for h in self.headers))
            self.assertFalse(self.calls)
        finally:
            await client.close()
        config["servers"][0].pop("allowedUsers")
        with self.assertRaisesRegex(McpError, "audience"):
            Client(self.ctx, config)

    async def test_mixed_batch_waits_then_executes_local_once_and_remote_rejected(self):
        count = []

        def local_tool(user):
            count.append(user)
            return "local result"

        self.app.tools["local_tool"] = local_tool
        self.app.tool_definitions.append(
            {
                "type": "function",
                "function": {
                    "name": "local_tool",
                    "parameters": {"type": "object", "properties": {"user": {"type": "string"}}},
                },
            }
        )
        extra = [{"id": "local-1", "function": {"name": "local_tool", "arguments": '{"user":"forged"}'}}]
        ident, _ = await self.batch(extra)
        with self.assertRaises(ToolApprovalPending):
            await self.client.approvals.execute(ident, self.context)
        self.assertFalse(count)
        row = self.client.store.rows("alice", batch=ident)[0]
        await self.client.approvals.decide(row["id"], "reject", {}, self.context)
        output = await self.client.approvals.execute(ident, self.context)
        self.assertEqual([m["tool_call_id"] for m in output], ["call-1", "local-1"])
        self.assertEqual(count, ["alice"])
        await self.client.approvals.execute(ident, self.context)
        self.assertEqual(count, ["alice"])
        self.assertFalse(self.calls)

    async def test_oauth_defaults_to_plaintext_storage(self):
        config = copy.deepcopy(self.config)
        config["oauthRedirectUri"] = "https://chat.example.com/ext/mcp_client/oauth/callback"
        server = config["servers"][0]
        server.update(auth={"mode": "user_oauth"}, oauthClientId="client", oauthIssuer=str(self.remote.make_url("/issuer")))
        client = Client(self.ctx, config)
        try:
            client.store.connect("alice", "work")
            client.save_oauth_client_secret(client.servers[0], "alice", "fixture-secret")
            binding = client.store.binding("alice", "work")
            self.assertEqual(json.loads(binding["clientSecret"])["secret"], "fixture-secret")
            self.assertEqual(client.oauth_client_secret(client.servers[0], "alice"), "fixture-secret")
            self.mode = "oauth_challenge"
            url = await client.oauth.start(client.servers[0], self.context)
            from urllib.parse import parse_qs, urlsplit
            state = parse_qs(urlsplit(url).query)["state"][0]
            with client.store.connection() as db:
                payload = db.execute("SELECT payload FROM mcp_oauth WHERE id=?", (digest(state),)).fetchone()
            self.assertIsNotNone(payload)
            self.assertIn("verifier", json.loads(payload[0]))
        finally:
            await client.close()

    async def test_oauth_pkce_host_protected_storage_refresh_and_callback_replay(self):
        import base64
        import hashlib
        import secrets
        from urllib.parse import parse_qs, urlsplit

        # A host-owned opaque test vault verifies the optional protection hooks.
        vault = {}

        def protect(value, purpose):
            key = secrets.token_hex(24)
            vault[key] = (purpose, value)
            return key

        def unprotect(value, purpose):
            stored_purpose, plain = vault[value]
            self.assertEqual(purpose, stored_purpose)
            return plain

        config = copy.deepcopy(self.config)
        # The inbound browser callback is not an outbound MCP destination, so it
        # need not use one of the network policy's allowed outbound ports.
        config["oauthRedirectUri"] = "https://chat.example.com:5001/ext/mcp_client/oauth/callback"
        server = config["servers"][0]
        server.update(
            auth={"mode": "user_oauth"},
            oauthClientId="registered-client",
            oauthIssuer=str(self.remote.make_url("/issuer")),
            oauthScopes=["tools.read"],
        )
        client = Client(self.ctx, config, {"protect": protect, "unprotect": unprotect})
        server = client.servers[0]
        try:
            await client.access(server, self.context, "connect")
            client.store.connect("alice", "work")
            before_secret = client.store.binding("alice", "work")["revision"]
            client.save_oauth_client_secret(server, "alice", "fixture-client-secret")
            self.assertEqual(client.store.binding("alice", "work")["revision"], before_secret)
            self.assertNotIn("fixture-client-secret", str(client.store.binding("alice", "work")))
            self.assertIsNone(client.oauth_client_secret({**server, "displayName": "Changed"}, "alice"))
            self.mode = "oauth_challenge"
            url = await client.oauth.start(server, self.context)
            with self.assertRaises(McpError) as duplicate:
                await client.oauth.start(server, self.context)
            self.assertEqual(duplicate.exception.code, "busy")
            params = parse_qs(urlsplit(url).query)
            self.assertEqual(params["code_challenge_method"], ["S256"])
            self.assertEqual(params["scope"], ["tools.read"])
            with client.store.connection() as db:
                self.assertIsNone(db.execute("SELECT 1 FROM mcp_oauth WHERE id=?", (params["state"][0],)).fetchone())
            with self.assertRaises(McpError) as invalid_code:
                await client.oauth.complete(SimpleNamespace(query={
                    "state": params["state"][0], "code": "x" * 8193,
                }), self.context)
            self.assertEqual(invalid_code.exception.code, "invalid_oauth_response")
            callback = SimpleNamespace(
                query={"state": params["state"][0], "code": "one-use-code", "iss": server["oauthIssuer"]}
            )
            with self.assertRaises(McpError):
                await client.oauth.complete(callback, {**self.context, "owner": "bob", "user": "bob"})
            self.assertEqual(await client.oauth.complete(callback, self.context), server["id"])
            await client.catalog(server, self.context, refresh=True)
            self.assertEqual(client.status("alice", server)["state"], "ready")
            from llms.extensions.mcp_client import oauth_callback_page
            callback_page = oauth_callback_page(True, server_id='"><img src=x>')
            self.assertIn('data-server-id="&quot;&gt;&lt;img src=x&gt;"', callback_page.text)
            self.assertIn("script-src 'nonce-", callback_page.headers["Content-Security-Policy"])
            self.assertIn("Close this tab", callback_page.text)
            exchanged = self.oauth_requests[0]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(exchanged["code_verifier"].encode()).digest())
                .decode()
                .rstrip("=")
            )
            self.assertEqual(challenge, params["code_challenge"][0])
            self.assertEqual(exchanged["resource"], str(self.remote.make_url("/mcp")))
            self.assertEqual(exchanged["client_secret"], "fixture-client-secret")
            stored = client.store.binding("alice", "work")
            self.assertNotIn("private-access", stored["credential"])
            with self.assertRaises(McpError):
                await client.oauth.complete(callback, self.context)
            handle = next(iter((await client.resolve(self.context, "mcp_work")).values()))
            await client.invoke(handle, {"text": "oauth"}, self.context, approved=True)
            self.assertTrue(any(h.get("Authorization") == "Bearer private-access-1" for h in self.headers))
            token = client.oauth.open(stored["credential"], "alice", "work")
            token["expiresAt"] = time.time() - 1
            client.store.credential("alice", "work", stored["revision"], client.oauth.seal(token, "alice", "work"))
            await client.catalog(server, self.context, refresh=True)
            self.assertEqual(self.oauth_requests[-1]["grant_type"], "refresh_token")
            self.assertEqual(self.oauth_requests[-1]["client_secret"], "fixture-client-secret")
            client.store.connect("alice", "work", disconnected=True, delete=True, preserve_client_secret=True)
            self.assertIsNone(client.store.binding("alice", "work")["credential"])
            self.assertIsNotNone(client.store.binding("alice", "work")["clientSecret"])
            with self.assertRaises(McpError):
                await client.invoke(handle, {"text": "revoked"}, self.context, approved=True)
            client.store.connect("alice", "work")
            again = parse_qs(urlsplit(await client.oauth.start(server, self.context)).query)
            self.assertNotEqual(again["state"], params["state"])
            await client.oauth.complete(SimpleNamespace(query={
                "state": again["state"][0], "code": "second-code", "iss": server["oauthIssuer"],
            }), self.context)
            self.assertEqual(self.oauth_requests[-1]["grant_type"], "authorization_code")
            self.assertEqual(self.oauth_requests[-1]["client_secret"], "fixture-client-secret")
            self.assertIsNotNone(client.store.binding("alice", "work")["credential"])
            client.store.connect("alice", "work", disconnected=True, delete=True)
            without_secret = dict(server, endpoint="https://api.githubcopilot.com/mcp/")
            with self.assertRaises(McpError) as missing:
                await client.oauth.start(without_secret, self.context)
            self.assertEqual(missing.exception.code, "oauth_client_secret_required")
        finally:
            await client.close()

    async def test_oauth_callback_route_connects_and_loads_tools(self):
        import secrets
        from urllib.parse import parse_qs, urlsplit

        vault = {}
        def protect(value, purpose):
            key = secrets.token_hex(24)
            vault[key] = (purpose, value)
            return key
        def unprotect(value, purpose):
            saved_purpose, value = vault[value]
            self.assertEqual(purpose, saved_purpose)
            return value

        self.client.hooks.update(protect=protect, unprotect=unprotect)
        self.client.config["oauthRedirectUri"] = "https://chat.example.com:5001/ext/mcp_client/oauth/callback"
        server = self.client.servers[0]
        server.update(auth={"mode": "user_oauth"}, oauthClientId="registered-client",
                      oauthIssuer=str(self.remote.make_url("/issuer")))
        self.client.save_oauth_client_secret(server, "alice", "fixture-client-secret")
        self.mode = "oauth_challenge"
        headers = {"Test-User": "alice", "X-Mcp-Client": "1"}
        started = await self.http.post("/ext/mcp_client/connections/work/connect", headers=headers, json={})
        self.assertEqual(started.status, 200)
        url = (await started.json())["authorizationUrl"]
        state = parse_qs(urlsplit(url).query)["state"][0]
        duplicate = await self.http.post("/ext/mcp_client/connections/work/connect", headers=headers, json={})
        self.assertEqual(duplicate.status, 409)
        callback = await self.http.get("/ext/mcp_client/oauth/callback", headers={"Test-User": "alice"},
                                       params={"state": state, "code": "one-use-code", "iss": server["oauthIssuer"]})
        self.assertEqual(callback.status, 200)
        self.assertIn("Connection authorized", await callback.text())
        self.assertEqual(self.client.status("alice", server)["state"], "ready")
        self.assertIn("tools/list", self.methods)

    async def test_real_scheduler_pause_approve_and_resume_canonical_history(self):
        app_module = importlib.import_module("llms.extensions.app")
        from tests.test_chat_completion_refactor import MockProvider

        remote_name = alias("work", "echo")
        provider = MockProvider(
            responses=[
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "scheduler-call",
                                        "type": "function",
                                        "function": {"name": remote_name, "arguments": '{"text":"before"}'},
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "id": "final-response",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                    "choices": [{"message": {"role": "assistant", "content": "Finished"}}],
                },
            ]
        )
        self.app.loading_messages = ["Working", "Thinking"]
        self.app.chat_tool_filters.clear()
        appctx = ExtensionContext(self.app, str(Path(__file__).resolve().parents[1] / "llms/extensions/app"))
        original_handlers = dict(main.g_handlers)
        main.g_handlers.clear()
        main.g_handlers["fixture"] = provider
        try:
            with patch.object(app_module, "g_db", self.db):
                app_module.install(appctx)
                # Control execution deterministically, exercising the actual slice
                # implementation while preventing the coordinator from racing the test.
                self.app.agent_scheduler.wake = lambda: None
                self.app.agent_requests[self.run] = self.context["request"]
                await self.db.update_thread_async(self.thread, {"metadata": {"tools": "mcp_work"}}, user="alice")
                await self.app.agent_scheduler.execute_slice(self.db.get_agent_run(self.run, user="alice"))
                self.assertEqual(self.db.get_agent_run(self.run, user="alice")["status"], "waiting_approval")
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(len(self.calls), 0)
                row = self.client.store.rows("alice", thread=self.thread)[0]
                await self.client.approvals.decide(row["id"], "approve", {"args": {"text": "after"}}, self.context)
                self.assertEqual(self.db.get_agent_run(self.run, user="alice")["status"], "queued")
                self.db.update_agent_run(self.run, {"status": "running"})
                await self.app.agent_scheduler.execute_slice(self.db.get_agent_run(self.run, user="alice"))
                self.assertEqual(self.db.get_agent_run(self.run, user="alice")["status"], "completed")
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(provider.call_count, 2)
                messages = json.loads(self.db.get_thread(self.thread, user="alice")["messages"])
                self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool", "assistant"])
                self.assertEqual(messages[2]["tool_call_id"], "scheduler-call")
                self.assertIn("after", messages[2]["content"])
                self.assertEqual(messages[-1]["content"], "Finished")
        finally:
            main.g_handlers.clear()
            main.g_handlers.update(original_handlers)
            if hasattr(self.app, "agent_scheduler"):
                await self.app.agent_scheduler.stop()

    async def test_reconcile_uncertain_result_keeps_one_copy_of_user_history(self):
        app_module = importlib.import_module("llms.extensions.app")
        from tests.test_chat_completion_refactor import MockProvider

        provider = MockProvider(responses=[
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{
                "id": "uncertain-call", "type": "function",
                "function": {"name": alias("work", "echo"), "arguments": '{"text":"once"}'},
            }]}}]},
            {"usage": {"prompt_tokens": 10, "completion_tokens": 4},
             "choices": [{"message": {"role": "assistant", "content": "The result is uncertain"}}]},
        ])
        self.app.loading_messages = ["Working", "Thinking"]
        self.app.chat_tool_filters.clear()
        appctx = ExtensionContext(self.app, str(Path(__file__).resolve().parents[1] / "llms/extensions/app"))
        original_handlers = dict(main.g_handlers)
        main.g_handlers.clear()
        main.g_handlers["fixture"] = provider
        try:
            with patch.object(app_module, "g_db", self.db):
                app_module.install(appctx)
                self.app.agent_scheduler.wake = lambda: None
                self.app.agent_requests[self.run] = self.context["request"]
                await self.db.update_thread_async(self.thread, {
                    "messages": [{"role": "system", "content": "Test instructions"},
                                 {"role": "user", "content": "Ask once"}],
                    "metadata": {"tools": "mcp_work"},
                }, user="alice")
                await asyncio.wait_for(
                    self.app.agent_scheduler.execute_slice(self.db.get_agent_run(self.run, user="alice")), 5
                )
                row = self.client.store.rows("alice", thread=self.thread)[0]
                self.mode = "lost"
                result = await asyncio.wait_for(
                    self.client.approvals.decide(row["id"], "approve", {}, self.context), 5
                )
                self.assertEqual(result["status"], "outcome_unknown")
                self.assertEqual(len(self.calls), 1)
                await asyncio.wait_for(self.client.approvals.decide(
                    row["id"], "reconcile", {"decision": "continue_without_replay"}, self.context
                ), 5)
                self.db.update_agent_run(self.run, {"status": "running"})
                await asyncio.wait_for(
                    self.app.agent_scheduler.execute_slice(self.db.get_agent_run(self.run, user="alice")), 5
                )
                messages = json.loads(self.db.get_thread(self.thread, user="alice")["messages"])
                self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "tool", "assistant"])
                self.assertEqual([m["content"] for m in messages if m["role"] == "user"], ["Ask once"])
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.db.get_agent_run(self.run, user="alice")["status"], "completed")
        finally:
            main.g_handlers.clear()
            main.g_handlers.update(original_handlers)
            if hasattr(self.app, "agent_scheduler"):
                await self.app.agent_scheduler.stop()

    async def test_single_call_no_queue_configuration_and_recovery_of_saved_result(self):
        self.client.limits.update(callsPerPrincipal=1, queueLength=0)
        ident, _ = await self.batch()
        row = self.client.store.rows("alice", batch=ident)[0]
        self.assertTrue(self.client.store.claim(row["id"], "alice"))
        self.client.store.invocation_claim(row["invocationId"], "alice")
        result = {"source": "mcp_client", "serverId": "work", "tool": "echo", "content": [], "resources": []}
        self.client.store.invocation_state(row["invocationId"], "alice", "completed", result)
        with self.client.store.connection() as db:
            db.execute("UPDATE mcp_approval SET updated=? WHERE id=?", (time.time() - 601, row["id"]))
        restored = self.client.store.rows("alice", batch=ident)[0]
        self.assertEqual(restored["status"], "completed")
        self.assertEqual(restored["result"], result)
        await self.client.approvals.execute(ident, self.context)
        self.assertFalse(self.calls)

    async def test_tools_endpoint_integration_and_cannot_bypass_approval(self):
        from llms.extensions.tools import install as install_tools

        toolctx = ExtensionContext(self.app, str(Path(__file__).resolve().parents[1] / "llms/extensions/tools"))
        install_tools(toolctx)
        handler = next(h for p, h, _ in self.app.server_add_get if p == "/ext/tools")
        response = await handler(self.context["request"])
        catalog = json.loads(response.text)
        self.assertIn("mcp_work", catalog["groups"])
        name = catalog["definitions"][0]["function"]["name"]
        handler = next(h for p, h, _ in self.app.server_add_post if p == "/ext/tools/exec/{name}")

        async def body():
            return {"text": "attempt bypass"}

        request = SimpleNamespace(headers=self.context["request"].headers, match_info={"name": name}, json=body)
        response = await handler(request)
        self.assertEqual(response.status, 403)
        self.assertEqual(json.loads(response.text)["responseStatus"]["errorCode"], "approval_required")
        self.assertFalse(self.calls)

    async def test_checkpoint_failure_after_dispatch_never_retries_provider(self):
        from tests.test_chat_completion_refactor import MockProvider

        self.client.servers[0]["approval"] = "never"
        provider = MockProvider(
            responses=[
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "once",
                                        "function": {"name": alias("work", "echo"), "arguments": '{"text":"x"}'},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        )

        async def checkpoint(chat, context):
            if chat["messages"][-1]["role"] == "tool":
                raise RuntimeError("simulated checkpoint failure")
            await self.db.update_thread_async(self.thread, {"messages": chat["messages"]}, user="alice")

        self.app.chat_tool_filters[:] = [checkpoint]
        self.context["tools"] = "mcp_work"
        original = dict(main.g_handlers)
        main.g_handlers.clear()
        main.g_handlers["fixture"] = provider
        try:
            with self.assertRaisesRegex(RuntimeError, "checkpoint failure"):
                await main.g_chat_completion(
                    {"model": "fixture", "messages": [{"role": "user", "content": "go"}]}, self.context
                )
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(provider.call_count, 1)
            self.assertTrue(self.context["remoteToolsDispatched"])
        finally:
            main.g_handlers.clear()
            main.g_handlers.update(original)


class SchemaTests(unittest.TestCase):
    def test_parameter_header_encoding_and_invalid_annotations(self):
        from llms.extensions.mcp_client.headers import header_mappings, parameter_headers
        schema = {"type": "object", "properties": {
            "owner": {"type": "string", "x-mcp-header": "owner"},
            "nested": {"type": "object", "properties": {
                "count": {"type": "integer", "x-mcp-header": "Count"},
                "active": {"type": "boolean", "x-mcp-header": "Active"},
            }},
        }}
        self.assertEqual(parameter_headers(schema, {
            "owner": "Hello, 世界", "nested": {"count": 42.0, "active": False},
        }), {"Mcp-Param-owner": "=?base64?SGVsbG8sIOS4lueVjA==?=",
             "Mcp-Param-Count": "42", "Mcp-Param-Active": "false"})
        self.assertEqual(parameter_headers(schema, {"owner": None}), {})
        self.assertEqual(parameter_headers(schema, {"owner": "=?base64?literal?="})["Mcp-Param-owner"],
                         "=?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?=")
        invalid = {"type": "object", "properties": {
            "owner": {"type": "string", "x-mcp-header": "owner"},
            "repo": {"type": "string", "x-mcp-header": "OWNER"},
        }}
        with self.assertRaises(McpError):
            header_mappings(invalid)
        invalid["properties"]["repo"]["x-mcp-header"] = "bad\r\nName"
        with self.assertRaises(McpError):
            header_mappings(invalid)

    def test_mcp_database_is_private_to_host_account(self):
        if os.name == "nt":
            self.skipTest("POSIX permissions are not available on Windows")
        from llms.extensions.mcp_client.store import Store
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mcp_client" / "mcp.sqlite"
            Store(path)
            self.assertEqual(path.parent.stat().st_mode & 0o077, 0)
            self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_authenticated_host_loads_mcp_connections_ui_without_servers(self):
        previous_app = main.g_app
        with tempfile.TemporaryDirectory() as directory:
            app = AppExtensions(argparse.Namespace(), {})
            app.config = {}
            app.get_user_path = lambda user=None: os.path.join(directory, "user", user or "default")
            extension = Path(__file__).resolve().parents[1] / "llms/extensions/mcp_client"
            auth_extension = Path(directory) / "auth_fixture"
            auth_extension.mkdir()
            (auth_extension / "__init__.py").write_text(
                "def __install__(ctx):\n    ctx.app.auth_provider = object()\n", encoding="utf-8"
            )
            try:
                # Filesystem discovery can return MCP before auth. Installation
                # must defer MCP until the authentication provider is available.
                with patch.object(main, "get_extensions_dirs", return_value=[str(extension), str(auth_extension)]):
                    app.extensions = main.install_extensions()
                self.assertEqual([item["name"] for item in app.extensions], ["auth_fixture", "mcp_client"])
                self.assertEqual(app.ui_extensions, [{"id": "mcp_client", "path": "/ext/mcp_client/index.mjs"}])
                self.assertIsNotNone(app.mcp_client)
                self.assertEqual(app.mcp_client.get_servers("alice"), [])
            finally:
                main.g_app = previous_app

    def test_unauthenticated_host_loads_mcp_ui_by_default_and_can_disable_it(self):
        previous_app = main.g_app
        try:
            with tempfile.TemporaryDirectory() as directory:
                app = AppExtensions(argparse.Namespace(), {})
                app.config = {}
                app.get_user_path = lambda user=None: os.path.join(directory, "user", user or "default")
                extension = Path(__file__).resolve().parents[1] / "llms/extensions/mcp_client"
                with patch.object(main, "get_extensions_dirs", return_value=[str(extension)]):
                    app.extensions = main.install_extensions()
                self.assertEqual(app.ui_extensions, [{"id": "mcp_client", "path": "/ext/mcp_client/index.mjs"}])
                self.assertIsNotNone(app.mcp_client)
                self.assertTrue(app.mcp_client.config["singleUserMode"])
                config = app.mcp_client.personal_configuration("default")
                self.assertTrue(config["canEdit"])
                saved = app.mcp_client.save_configuration("default", {
                    "revision": config["revision"],
                    "servers": [{"id": "example", "endpoint": "https://example.com/mcp", "allowedTools": ["*"]}],
                })
                self.assertEqual(saved["servers"][0]["id"], "example")
                self.assertEqual(app.mcp_client.server("example", "default")["_scope"], "personal:default")

                disabled = AppExtensions(argparse.Namespace(), {})
                disabled.config = {"mcp_client": {"enabled": False}}
                ctx = ExtensionContext(disabled, str(extension))
                install(ctx)
                self.assertTrue(ctx.disabled)
                self.assertFalse(hasattr(disabled, "mcp_client"))
        finally:
            main.g_app = previous_app

    def test_anonymous_host_requires_explicit_single_user_mode(self):
        ctx = SimpleNamespace(app=SimpleNamespace(), is_auth_enabled=lambda: False)
        with self.assertRaises(McpError) as denied:
            Client(ctx, {"servers": []})
        self.assertEqual(denied.exception.code, "invalid_configuration")

    def test_mcp_header_annotations_preserve_argument_validation(self):
        value = {"type": "object", "properties": {
            "sha": {"type": "string", "x-mcp-header": "X-MCP-SHA"},
            "x-mcp-header": {"type": "integer"}}, "required": ["sha", "x-mcp-header"]}
        original = copy.deepcopy(value)
        schema.check(value)
        schema.validate(value, {"sha": "main", "x-mcp-header": 1})
        with self.assertRaises(McpError):
            schema.validate(value, {"sha": 1, "x-mcp-header": 1})
        with self.assertRaises(McpError):
            schema.validate(value, {"sha": "main", "x-mcp-header": "bad"})
        self.assertEqual(value, original)

    def test_nested_refs_nullable_and_numeric_constraints(self):
        params = {
            "type": "object",
            "$defs": {"item": {"type": ["string", "null"], "minLength": 2}},
            "properties": {
                "items": {"type": "array", "items": {"$ref": "#/$defs/item"}, "uniqueItems": True},
                "count": {"type": "number", "multipleOf": 0.1, "minimum": 0},
            },
            "required": ["items"],
            "additionalProperties": False,
        }
        schema.check(params)
        schema.validate(params, {"items": ["hi", None], "count": 0.3})
        for value in (
            {"items": ["x"]},
            {"items": ["hi", "hi"]},
            {"items": [1]},
            {"items": [], "count": True},
            {"items": [], "extra": 1},
        ):
            with self.subTest(value=value), self.assertRaises(McpError):
                schema.validate(params, value)

    def test_unsupported_constraints_and_recursive_refs_fail_closed(self):
        for params in (
            {"$ref": "https://remote/schema"},
            {"pattern": "(a+)+$"},
            {"unevaluatedProperties": False},
            {"$defs": {"self": {"$ref": "#/$defs/self"}}, "$ref": "#/$defs/self"},
        ):
            with self.subTest(params=params), self.assertRaises(McpError):
                schema.check(params)

    def test_literal_properties_named_like_schema_keywords(self):
        params = {"type": "object", "properties": {"pattern": {"type": "string"}, "$id": {"type": "string"}}}
        schema.check(params)
        schema.validate(params, {"pattern": "literal"})

    def test_alias_collision_resistance_matches_csharp_algorithm(self):
        self.assertNotEqual(alias("work", "a-b"), alias("work", "a_b"))
        self.assertNotEqual(alias("work", "echo"), alias("work", "Echo"))
        self.assertLessEqual(len(alias("work", "a" * 512)), 64)
        self.assertEqual(
            alias("work", "echo"), "mcp_work_echo_" + __import__("hashlib").sha256(b"work\0echo").hexdigest()[:16]
        )

    def test_private_network_default_and_explicit_allowlist(self):
        policy = NetworkPolicy()
        for url in (
            "http://example.com/mcp",
            "https://127.0.0.1/mcp",
            "https://169.254.169.254/",
            "https://[::1]/",
            "https://user:pass@example.com",
            "https://example.com:444/mcp",
        ):
            with self.subTest(url=url), self.assertRaises(McpError):
                policy.uri(url)
        policy.uri("https://example.com/mcp")
        NetworkPolicy({"allowedPrivateNetworks": ["10.0.0.0/8"]}).uri("https://10.1.2.3/mcp")

    def test_result_mapping_structured_arrays_and_private_media(self):
        result = map_result(
            {
                "structuredContent": [1, 2],
                "content": [
                    {"type": "text", "text": "[1,2]"},
                    {"type": "resource_link", "uri": "file:///private/path", "name": "doc"},
                    {"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgo="},
                    {"type": "image", "mimeType": "image/svg+xml", "data": "evil"},
                ],
            },
            {"remoteName": "echo"},
            {"id": "work"},
            {"maxResponseBytes": 4096},
        )
        self.assertEqual(result["structuredContent"], [1, 2])
        self.assertEqual(result["content"][0]["type"], "reference")
        self.assertTrue(result["resources"][0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(result["content"][-1]["type"], "unsupported")
        with self.assertRaises(McpError):
            map_result(
                {"content": [{"type": "image", "mimeType": "image/png", "data": "SGVsbG8="}]},
                {"remoteName": "echo"},
                {"id": "work"},
                {"maxResponseBytes": 4096},
            )

    def test_dynamic_extension_import_shares_the_core_error_contract(self):
        import importlib.util
        import sys

        name = "mcp_fixture_dynamic"
        path = Path(__file__).resolve().parents[1] / "llms/extensions/mcp_client/__init__.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
            self.assertIs(module.McpError, McpError)
        finally:
            for key in list(sys.modules):
                if key == name or key.startswith(name + "."):
                    sys.modules.pop(key)

    def test_disabled_extension_has_no_ui_routes_or_clients(self):
        ctx = SimpleNamespace(config={"mcp_client": {"enabled": False}}, disabled=False)
        install(ctx)
        self.assertTrue(ctx.disabled)


if __name__ == "__main__":
    unittest.main()
