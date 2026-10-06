"""Public SIWC regressions using an isolated issuer and OpenSSL-signed ID tokens.

No production credentials, API calls, or paid inference are used.
"""

import asyncio
import base64
import copy
import importlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from llms.extensions.openai_auth import (
    OpenAiSubscriptionProvider,
    _auth,
    activate_subscription_provider,
    cleanup_active_flow,
    install,
    load,
    reload_providers_hook,
)
from llms.extensions.openai_auth.security import Options, Store, SubscriptionError, parse_object
from llms.main import OpenAiCompatible, create_error_response

main = importlib.import_module("llms.main")


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class OpenAiAuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("openssl"):
            raise unittest.SkipTest("OpenSSL is required for independent signed-identity fixtures")
        cls.key_dir = tempfile.TemporaryDirectory()
        cls.key_path = Path(cls.key_dir.name) / "key.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(cls.key_path)],
            check=True,
            capture_output=True,
        )
        modulus = subprocess.run(
            ["openssl", "rsa", "-in", str(cls.key_path), "-noout", "-modulus"], check=True, capture_output=True
        ).stdout
        cls.jwk = {
            "kid": "fixture",
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "n": b64(bytes.fromhex(modulus.decode().strip().split("=")[1])),
            "e": "AQAB",
        }

    @classmethod
    def tearDownClass(cls):
        cls.key_dir.cleanup()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved_handlers = dict(main.g_handlers)
        self.saved_app = main.g_app
        self.calls, self.inference, self.key_requests = [], [], 0
        self.model_bearers = []
        self.response_mode, self.token_status, self.refresh_calls = "complete", 200, 0
        self.delay_entered, self.delay_release = asyncio.Event(), asyncio.Event()
        self.delay_token = False
        self.token_body_override = None
        self.model_rows = [
            {"slug": "fixture-model", "display_name": "Fixture Model", "visibility": "list"},
            {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
        ]
        self.base = OpenAiCompatible(
            id="openai",
            name="OpenAI",
            api="https://api.openai.com/v1",
            api_key="sk-fixture",
            models={"fixture-model": {"id": "fixture-model", "name": "Fixture Model"}},
        )
        self.base.chat = AsyncMock(return_value={"fallback": True})
        main.g_handlers.clear()
        main.g_handlers["openai"] = self.base
        issuer = web.Application()
        issuer.router.add_post("/token", self.token_handler)
        issuer.router.add_get("/keys", self.keys_handler)
        issuer.router.add_get("/models", self.models_handler)
        issuer.router.add_post("/responses", self.responses_handler)
        issuer.router.add_get("/.well-known/openid-configuration", self.discovery_handler)
        issuer.router.add_post("/revoke", self.revoke_handler)

        async def redirect_handler(request):
            raise web.HTTPFound("/models")

        issuer.router.add_get("/redirect", redirect_handler)
        self.server = TestServer(issuer)
        await self.server.start_server()
        origin = str(self.server.make_url("/")).rstrip("/")
        self.options = Options(
            issuer=origin,
            authorization_url=origin + "/authorize",
            token_url=origin + "/token",
            jwks_url=origin + "/keys",
            models_url=origin + "/models",
            responses_url=origin + "/responses",
        )
        self.routes = {}
        self.ctx = SimpleNamespace(
            get_user_path=lambda user=None: str(Path(self.temp.name) / "user" / (user or "default")),
            openai_subscription_options=self.options,
            app=SimpleNamespace(),
            threads=None,
            register_provider_handler=lambda name, provider: main.g_handlers.__setitem__(name, provider),
            register_shutdown_handler=lambda fn: None,
            log=lambda text: None,
            add_get=lambda path, fn: self.routes.__setitem__(("GET", path), fn),
            add_post=lambda path, fn: self.routes.__setitem__(("POST", path), fn),
            assert_username=lambda request: request.headers.get("Test-User", "default"),
            create_error_response=create_error_response,
        )
        install(self.ctx)
        self.auth = _auth(self.ctx)
        app = web.Application()
        for (method, path), fn in self.routes.items():
            app.router.add_route(method, "/" + path, fn)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        main.g_app = None

    async def asyncTearDown(self):
        self.delay_release.set()
        await self.client.close()
        await self.server.close()
        await cleanup_active_flow()
        main.g_handlers.clear()
        main.g_handlers.update(self.saved_handlers)
        main.g_app = self.saved_app
        self.temp.cleanup()

    def jwt(self, claims, header=None):
        header = header or {"alg": "RS256", "kid": "fixture"}
        data = (b64(json.dumps(header).encode()) + "." + b64(json.dumps(claims).encode())).encode()
        signature = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(self.key_path)], input=data, capture_output=True, check=True
        ).stdout
        return data.decode() + "." + b64(signature)

    def claims(self, user="alice", nonce=None, **overrides):
        return {
            "iss": self.options.issuer,
            "aud": "oaiapp_fixture",
            "sub": user,
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
            "nonce": nonce,
            "email": user + "@example.test",
            "name": user.title(),
            **overrides,
        }

    def token_response(self, user="alice", nonce=None, **overrides):
        return {
            "access_token": "access-" + user,
            "refresh_token": "refresh-" + user,
            "id_token": self.jwt(self.claims(user, nonce)),
            "expires_in": 3600,
            "scope": self.options.scope,
            "token_type": "Bearer",
            **overrides,
        }

    def save_grant(self, user="alice", **overrides):
        value = self.auth.credentials(self.token_response(user), "oaiapp_fixture", self.claims(user))
        value.update(overrides)
        self.auth.store.save(user, value)
        return value

    def callback_url(self, user="alice", **overrides):
        flow = self.auth.pending[user]
        values = {"state": flow["state"], "code": user, "client_id": "oaiapp_fixture", **overrides}
        return flow["redirect"] + "?" + urlencode(values)

    async def sign_in(self, user="alice"):
        self.auth.connect(user)
        await self.auth.callback(user, self.callback_url(user))
        activate_subscription_provider(self.ctx)

    async def token_handler(self, request):
        self.assertEqual(request.content_type, "application/x-www-form-urlencoded")
        form = dict(await request.post())
        self.calls.append(form)
        if self.delay_token:
            self.delay_entered.set()
            await self.delay_release.wait()
        if self.token_status != 200:
            return web.Response(status=self.token_status, text="SECRET access-operator@example.test")
        if self.token_body_override is not None:
            return web.json_response(self.token_body_override)
        if form["grant_type"] == "refresh_token":
            self.refresh_calls += 1
            user = form["refresh_token"].removeprefix("refresh-")
            return web.json_response(
                self.token_response(user, access_token="rotated-" + user, refresh_token="refresh-" + user)
            )
        user = form["code"]
        # The callback consumed its flow, but the authorized nonce is captured by connect.
        nonce = self.nonces[user]
        return web.json_response(self.token_response(user, nonce))

    async def keys_handler(self, request):
        self.key_requests += 1
        return web.json_response({"keys": [self.jwk]})

    async def models_handler(self, request):
        self.model_bearers.append(request.headers.get("Authorization"))
        return web.json_response({"models": self.model_rows})

    async def discovery_handler(self, request):
        return web.json_response(
            {"issuer": self.options.issuer, "revocation_endpoint": self.options.issuer + "/revoke"}
        )

    async def revoke_handler(self, request):
        self.revoked = dict(await request.post())
        return web.Response(status=200)

    async def responses_handler(self, request):
        payload = await request.json()
        self.inference.append((request.headers.get("Authorization"), payload))
        if self.response_mode == "401" and request.headers["Authorization"].startswith("Bearer access-"):
            return web.Response(status=401)
        if self.response_mode == "500":
            return web.Response(status=500, text="SECRET operator token")
        if self.response_mode == "redirect":
            raise web.HTTPTemporaryRedirect("/responses")
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)

        async def event(value):
            await response.write(("data:" + json.dumps(value) + "\n\n").encode())

        await event(
            {
                "type": "response.created",
                "response": {"id": "resp_fixture", "model": payload["model"], "created_at": 1700000000},
            }
        )
        await event({"type": "response.output_text.delta", "delta": "Hello "})
        await event(
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {"type": "function_call", "call_id": "call-fixture", "name": "lookup", "arguments": ""},
            }
        )
        await event({"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"q":"stars"}'})
        if self.response_mode == "disconnect":
            self.auth.disconnect("alice")
        if self.response_mode == "malformed":
            await response.write(b"data:invalid\n\n")
        elif self.response_mode == "failed":
            await event({"type": "response.failed", "response": {"error": {"message": "SECRET fixture"}}})
        elif self.response_mode != "truncated":
            await event(
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "usage": {
                            "input_tokens": 12,
                            "output_tokens": 18,
                            "total_tokens": 30,
                            "input_tokens_details": {"cached_tokens": 4},
                        },
                    },
                }
            )
        await response.write_eof()
        return response

    def connect(self, user="alice"):
        value = self.auth.connect(user)
        if not hasattr(self, "nonces"):
            self.nonces = {}
        self.nonces[user] = self.auth.pending[user]["nonce"]
        return value

    async def test_public_authorization_and_verified_exchange(self):
        value = self.connect()
        query = parse_qs(urlsplit(value["auth_url"]).query)
        self.assertEqual(query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(query["scope"], [self.options.scope])
        self.assertEqual(query["resource"], ["https://api.openai.com/v1"])
        self.assertEqual(query["agent_name_hint"], ["llms-py"])
        self.assertIn("nonce", query)
        self.assertEqual(len(query["code_challenge"][0]), 43)
        self.assertNotIn("codex_cli_simplified_flow", query)
        await self.auth.callback("alice", self.callback_url())
        grant = self.auth.store.load("alice")
        self.assertEqual(grant["subject"], "alice")
        self.assertTrue(grant["plan_enabled"])
        self.assertEqual(self.calls[0]["client_id"], "oaiapp_fixture")
        self.assertEqual(self.calls[0]["resource"], self.options.resource)
        self.assertEqual(self.calls[0]["redirect_uri"], value["redirect_uri"])
        self.assertNotIn("alice", json.dumps(value))
        if os.name != "nt":
            self.assertEqual(os.stat(self.auth.store.path("alice")).st_mode & 0o777, 0o600)
        self.assertEqual(self.auth.store.host_id(), Store(self.ctx).host_id())
        self.assertEqual(self.key_requests, 1)

    async def test_signed_identity_rejects_invalid_claims_and_signature(self):
        for overrides in (
            {"iss": "https://evil.test"},
            {"aud": "other"},
            {"exp": 1},
            {"exp": True},
            {"nonce": "wrong"},
            {"sub": ""},
            {"nbf": int(time.time()) + 300},
            {"iat": int(time.time()) + 300},
            {"aud": ["oaiapp_fixture", "other"]},
            {"azp": "other"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(SubscriptionError):
                await self.auth.identity.verify(
                    self.jwt({**self.claims(nonce="expected"), **overrides}), "oaiapp_fixture", "expected"
                )
        for header in (
            {"alg": "none", "kid": "fixture"},
            {"alg": "HS256", "kid": "fixture"},
            {"alg": "RS256", "kid": "unknown"},
            {"alg": "RS256", "kid": "fixture", "crit": ["unsupported"]},
        ):
            with self.subTest(header=header), self.assertRaises(SubscriptionError):
                await self.auth.identity.verify(self.jwt(self.claims(), header), "oaiapp_fixture")
        jwt = self.jwt(self.claims())
        with self.assertRaises(SubscriptionError):
            await self.auth.identity.verify(jwt.rsplit(".", 1)[0] + "." + b64(b"\x00" * 256), "oaiapp_fixture")
        good = await self.auth.identity.verify(
            self.jwt(self.claims(aud=["oaiapp_fixture", "other"], azp="oaiapp_fixture")), "oaiapp_fixture"
        )
        self.assertEqual(good["sub"], "alice")

    async def test_callback_validates_owner_state_redirect_duplicates_and_client(self):
        self.connect()
        self.connect("bob")
        alice = self.callback_url()
        for url in (
            "bare-code",
            self.callback_url(state="wrong"),
            alice.replace("127.0.0.1", "localhost"),
            alice.replace("/auth/callback", "/callback"),
            alice + "&state=duplicate",
            alice + "#fragment",
            self.callback_url(client_id="dynamic_agent_client"),
            self.callback_url(client_id=""),
        ):
            with self.subTest(url=url), self.assertRaises(SubscriptionError):
                await self.auth.callback("alice", url)
        with self.assertRaises(SubscriptionError):
            await self.auth.callback("bob", alice)
        self.assertEqual(self.calls, [])
        await self.auth.callback("alice", alice)
        with self.assertRaises(SubscriptionError):
            await self.auth.callback("alice", alice)
        self.assertTrue(self.auth.has_pending("bob"))
        self.assertEqual(len(self.calls), 1)

    async def test_expired_flow_and_error_callback(self):
        self.connect()
        self.auth.pending["alice"]["created_at"] -= 601
        with self.assertRaises(SubscriptionError):
            await self.auth.callback("alice", self.callback_url())
        self.connect()
        with self.assertRaises(SubscriptionError):
            await self.auth.callback("alice", self.callback_url(error="access_denied", error_description="SECRET"))
        self.assertFalse(self.auth.has_pending("alice"))
        self.assertEqual(self.calls, [])

    async def test_uncertain_exchange_is_consumed_and_redacted(self):
        self.connect()
        url = self.callback_url()
        self.token_status = 500
        with self.assertRaises(SubscriptionError) as result:
            await self.auth.callback("alice", url)
        self.assertNotIn("SECRET", str(result.exception))
        with self.assertRaises(SubscriptionError):
            await self.auth.callback("alice", url)
        self.assertEqual(len(self.calls), 1)

    async def test_declined_plan_retains_identity_but_disables_inference(self):
        self.connect()
        self.token_body_override = self.token_response(nonce=self.nonces["alice"], scope="openid profile email")
        await self.auth.callback("alice", self.callback_url())
        self.assertEqual(self.auth.store.load("alice")["subject"], "alice")
        self.assertFalse(self.auth.store.load("alice")["plan_enabled"])
        self.assertEqual(await self.auth.models("alice"), [])
        provider = OpenAiSubscriptionProvider(self.ctx, self.base)
        with self.assertRaises(SubscriptionError):
            await provider.chat({"model": "fixture-model", "messages": []}, {"user": "alice"})
        self.base.chat.assert_not_awaited()

    async def test_returning_registration_rejects_new_client_and_identity(self):
        self.save_grant()
        value = self.connect()
        query = parse_qs(urlsplit(value["auth_url"]).query)
        self.assertEqual(query["client_id"], ["oaiapp_fixture"])
        self.assertNotIn("agent_name_hint", query)
        self.assertNotIn("id_token_hint", query)
        with self.assertRaises(SubscriptionError):
            await self.auth.callback("alice", self.callback_url(client_id="oaiapp_other"))
        self.token_body_override = self.token_response("bob", nonce=self.nonces["alice"])
        with self.assertRaises(SubscriptionError):
            await self.auth.callback("alice", self.callback_url())
        self.assertEqual(self.auth.store.load("alice")["subject"], "alice")

    async def test_late_callback_cannot_resurrect_disconnect_or_replace_new_flow(self):
        for mutation in (lambda: self.auth.disconnect("alice"), lambda: self.connect()):
            self.connect()
            self.delay_token = True
            self.delay_entered.clear()
            self.delay_release.clear()
            # Hold a valid response for the old flow, even after the new nonce is generated.
            self.token_body_override = self.token_response(nonce=self.nonces["alice"])
            task = asyncio.create_task(self.auth.callback("alice", self.callback_url()))
            await self.delay_entered.wait()
            mutation()
            self.delay_release.set()
            with self.assertRaises(SubscriptionError):
                await task
            self.assertIsNone(self.auth.store.load("alice"))

    async def test_credentials_are_exactly_user_scoped(self):
        self.save_grant("admin")
        self.save_grant("bob")
        self.assertIsNone(self.auth.store.load("alice"))
        self.assertIsNone(self.auth.store.load())
        self.assertIsNone(await self.auth.valid_credentials("alice"))
        self.auth.disconnect("bob")
        self.assertEqual(self.auth.store.load("admin")["subject"], "admin")
        self.assertFalse(Path(self.auth.store.path("bob")).exists())
        for user in ("../admin", "a/b", "a\\b", ".."):
            with self.subTest(user=user), self.assertRaises(SubscriptionError):
                self.auth.store.load(user)

    async def test_atomic_credentials_and_symlink_rejection(self):
        previous = self.save_grant()
        with (
            patch("llms.extensions.openai_auth.security.os.replace", side_effect=OSError("fixture")),
            self.assertRaises(OSError),
        ):
            self.auth.store.save("alice", {"access_token": "new"})
        self.assertEqual(self.auth.store.load("alice"), previous)
        self.assertEqual(list(Path(self.auth.store.path("alice")).parent.glob(".openai-*")), [])
        self.auth.store.disconnect("alice")
        Path(self.auth.store.path("alice")).symlink_to(self.auth.store.path("bob"))
        with self.assertRaises(SubscriptionError):
            self.auth.store.load("alice")
        with self.assertRaises(SubscriptionError):
            self.auth.store.save("alice", previous)

    async def test_serial_refresh_rotation_and_earliest_time(self):
        self.save_grant(expires_at=time.time() + 100)
        grants = await asyncio.gather(*(self.auth.valid_credentials("alice") for _ in range(8)))
        self.assertEqual(self.refresh_calls, 1)
        self.assertTrue(all(g["access_token"] == "rotated-alice" for g in grants))
        self.assertEqual(self.calls[0]["client_id"], "oaiapp_fixture")
        self.assertEqual(self.calls[0]["resource"], self.options.resource)
        self.assertNotIn("scope", self.calls[0])
        self.save_grant(expires_at=time.time() + 100, earliest_refresh_at=int(time.time()) + 200)
        self.assertEqual((await self.auth.valid_credentials("alice"))["access_token"], "access-alice")
        self.assertEqual(self.refresh_calls, 1)
        self.assertEqual(
            (await self.auth.valid_credentials("alice", rejected_token="access-alice"))["access_token"], "rotated-alice"
        )
        self.assertEqual(self.refresh_calls, 2)

    async def test_refresh_failure_falls_back_only_to_unexpired_same_grant(self):
        self.save_grant(expires_at=time.time() + 100)
        self.token_status = 500
        self.assertEqual((await self.auth.valid_credentials("alice"))["access_token"], "access-alice")
        with self.assertRaises(SubscriptionError):
            await self.auth.valid_credentials("alice", rejected_token="access-alice")
        self.save_grant(expires_at=time.time() - 1)
        with self.assertRaises(SubscriptionError):
            await self.auth.valid_credentials("alice")
        self.assertEqual(len(self.calls), 3)

    async def test_late_refresh_cannot_resurrect_disconnected_grant(self):
        self.save_grant(expires_at=time.time() + 100)
        self.delay_token = True
        task = asyncio.create_task(self.auth.valid_credentials("alice"))
        await self.delay_entered.wait()
        self.auth.disconnect("alice")
        self.delay_release.set()
        with self.assertRaises(SubscriptionError):
            await task
        self.assertIsNone(self.auth.store.load("alice"))

    async def test_refresh_cannot_change_verified_subject(self):
        self.save_grant(expires_at=time.time() - 1)
        self.token_body_override = self.token_response("bob")
        with self.assertRaises(SubscriptionError):
            await self.auth.valid_credentials("alice")
        self.assertEqual(self.auth.store.load("alice")["subject"], "alice")

    async def test_import_is_disabled_without_host_authorization(self):
        response = await self.client.post("/import_codex", headers={"Test-User": "alice"}, json={})
        self.assertEqual(response.status, 400)
        self.assertIn("disabled", await response.text())
        self.assertIsNone(self.auth.store.load("alice"))
        self.assertEqual(self.key_requests, 0)

    async def test_import_requires_signed_application_identity_and_protected_record(self):
        path = Path(self.temp.name) / "import.json"
        grant = self.save_grant()
        self.auth.store.disconnect("alice")
        path.write_text(json.dumps(grant))
        self.options.local_credentials_path = lambda user: str(path) if user == "alice" else None
        self.options.can_import_local_credentials = lambda request: request.headers.get("Test-User") == "alice"
        response = await self.client.post("/import_codex", headers={"Test-User": "bob"}, json={})
        self.assertEqual(response.status, 400)
        response = await self.client.post("/import_codex", headers={"Test-User": "alice"}, json={})
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.auth.store.load("alice")["subject"], "alice")
        grant["id_token"] = "unsigned.fake.signature"
        path.write_text(json.dumps(grant))
        response = await self.client.post("/import_codex", headers={"Test-User": "alice"}, json={})
        self.assertEqual(response.status, 400)

    async def test_status_is_private_and_disconnect_keeps_other_users_and_registration(self):
        alice = self.save_grant()
        self.save_grant("bob")
        activate_subscription_provider(self.ctx)
        response = await self.client.get("/status", headers={"Test-User": "alice"})
        body = await response.json()
        self.assertEqual(body["email"], "alice@example.test")
        self.assertFalse(body["has_codex_auth"])
        self.assertTrue(body["has_api_key"])
        self.assertTrue(body["api_key_disabled"])
        self.assertFalse(body["api_key_active"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn(alice["access_token"], json.dumps(body))
        self.assertNotIn(alice["id_token"], json.dumps(body))
        response = await self.client.post("/disconnect", headers={"Test-User": "alice"}, json={})
        self.assertTrue((await response.json())["remote_revocation_confirmed"])
        self.assertEqual(self.revoked["token_type_hint"], "refresh_token")
        self.assertIsNone(self.auth.store.load("alice"))
        self.assertIsInstance(main.g_handlers["openai"], OpenAiSubscriptionProvider)
        self.assertIsNotNone(self.auth.store.load("bob"))
        alice_status = await (await self.client.get("/status", headers={"Test-User": "alice"})).json()
        bob_status = await (await self.client.get("/status", headers={"Test-User": "bob"})).json()
        self.assertTrue(alice_status["api_key_active"])
        self.assertFalse(alice_status["api_key_disabled"])
        self.assertTrue(bob_status["api_key_disabled"])
        query = parse_qs(urlsplit(self.connect()["auth_url"]).query)
        self.assertEqual(query["client_id"], ["oaiapp_fixture"])
        self.assertNotIn("access_token", self.auth.store.registration("alice"))
        self.assertNotIn("id_token", self.auth.store.registration("alice"))

    async def test_remote_revocation_failure_still_disconnects_locally(self):
        self.save_grant()
        with patch.object(self.auth.http, "revoke", AsyncMock(return_value=False)):
            response = await self.client.post("/disconnect", headers={"Test-User": "alice"}, json={})
        self.assertFalse((await response.json())["remote_revocation_confirmed"])
        self.assertIsNone(self.auth.store.load("alice"))

    async def test_model_catalog_is_filtered_and_never_reused_across_users(self):
        self.save_grant()
        self.save_grant("bob")
        alice = await self.auth.models("alice")
        self.assertEqual([m["id"] for m in alice], ["fixture-model"])
        self.model_rows = [{"slug": "bob-model", "display_name": "Bob Model", "visibility": "list"}]
        bob = await self.auth.models("bob")
        self.assertEqual([m["id"] for m in bob], ["bob-model"])
        self.assertEqual(await self.auth.models("alice"), alice)
        self.assertEqual(self.model_bearers, ["Bearer access-alice", "Bearer access-bob"])
        self.auth.disconnect("alice")
        with self.assertRaises(SubscriptionError):
            await self.auth.models("alice")

    async def test_request_models_use_the_signed_in_users_subscription_and_never_fail_before_sign_in(self):
        self.save_grant()
        self.model_rows = [{"slug": "plan-model", "display_name": "Plan Model", "visibility": "list"}]
        main.g_app = SimpleNamespace(
            openai_subscription_auth=self.auth,
            is_auth_enabled=lambda: True,
            get_username=lambda request: request.headers.get("Test-User"),
        )

        def openai(models):
            return [m["id"] for m in models if m["provider"] == "openai"]

        alice = await main.get_request_models(SimpleNamespace(headers={"Test-User": "alice"}))
        # The subscription's models replace the API-key catalog's
        self.assertEqual(openai(alice), ["plan-model"])
        # Before sign-in there's no user, which the UI asks for models without
        anonymous = await main.get_request_models(SimpleNamespace(headers={}))
        self.assertEqual(openai(anonymous), ["fixture-model"])
        self.base.api_key = None
        anonymous = await main.get_request_models(SimpleNamespace(headers={}))
        self.assertEqual(openai(anonymous), [])

    async def test_real_stream_and_tool_translation_use_public_request_and_local_headers(self):
        self.save_grant()
        provider = OpenAiSubscriptionProvider(self.ctx, self.base)
        chat = {
            "model": "Fixture Model",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {"type": "function", "function": {"name": "lookup", "strict": True, "parameters": {"type": "object"}}}
            ],
        }
        original = copy.deepcopy(chat)
        result = await provider.chat(chat, {"user": "alice", "nostore": True})
        self.assertEqual(chat, original)
        self.assertEqual(result["model"], "fixture-model")
        self.assertEqual(result["usage"]["total_tokens"], 30)
        self.assertEqual(result["usage"]["prompt_tokens_details"]["cached_tokens"], 4)
        self.assertEqual(result["metadata"]["pricing"], "0/0")
        self.assertEqual(result["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"], '{"q":"stars"}')
        header, payload = self.inference[0]
        self.assertEqual(header, "Bearer access-alice")
        self.assertTrue(payload["stream"])
        self.assertFalse(payload["store"])
        self.assertEqual(payload["model"], "fixture-model")
        self.assertTrue(payload["tools"][0]["strict"])
        self.assertEqual(provider.headers, {})
        self.assertEqual(self.base.headers["Authorization"], "Bearer sk-fixture")

    async def test_parallel_chats_cannot_mix_bearer_credentials(self):
        self.save_grant()
        self.save_grant("bob")
        provider = OpenAiSubscriptionProvider(self.ctx, self.base)
        await asyncio.gather(
            *(
                provider.chat({"model": "fixture-model", "messages": []}, {"user": user, "nostore": True})
                for user in ("alice", "bob")
            )
        )
        self.assertEqual({h for h, _ in self.inference}, {"Bearer access-alice", "Bearer access-bob"})
        self.assertEqual(provider.headers, {})

    async def test_401_refreshes_once_without_paid_fallback(self):
        self.save_grant()
        self.response_mode = "401"
        provider = OpenAiSubscriptionProvider(self.ctx, self.base)
        result = await provider.chat({"model": "fixture-model", "messages": []}, {"user": "alice", "nostore": True})
        self.assertEqual(result["id"], "resp_fixture")
        self.assertEqual(len(self.inference), 2)
        self.assertEqual(self.refresh_calls, 1)
        self.base.chat.assert_not_awaited()

    async def test_errors_truncation_and_disconnect_never_become_success_or_paid_fallback(self):
        for mode in ("500", "redirect", "truncated", "failed", "malformed", "disconnect"):
            with self.subTest(mode=mode):
                self.save_grant()
                self.response_mode = mode
                provider = OpenAiSubscriptionProvider(self.ctx, self.base)
                writer = SimpleNamespace(write=AsyncMock(), flush=AsyncMock())
                with (
                    patch.object(provider, "stream_writer", return_value=writer),
                    self.assertRaises(SubscriptionError) as result,
                ):
                    await provider.chat({"model": "fixture-model", "messages": []}, {"user": "alice"})
                self.assertFalse(result.exception.retryable)
                self.assertNotIn("SECRET", str(result.exception))
                self.assertFalse(any(call.kwargs.get("final") for call in writer.write.call_args_list))
                self.base.chat.assert_not_awaited()
        self.assertEqual(len(self.inference), 6)

    async def test_no_outer_provider_retry_after_uncertain_stream(self):
        self.save_grant()
        self.response_mode = "truncated"
        await load(self.ctx)
        await self.auth.models("alice")
        with self.assertRaises(SubscriptionError):
            await main.g_chat_completion(
                {"model": "fixture-model", "messages": [], "tools": []}, {"user": "alice", "nostore": True}
            )
        self.assertEqual(len(self.inference), 1)

    async def test_legacy_or_invalid_grants_require_reconnect_and_never_fall_back(self):
        for value in (
            {"access_token": "legacy-codex", "client_id": "app_EMoamEEZ73f0CkXaXp7hrann"},
            {"access_token": ""},
        ):
            self.auth.store.save("alice", value)
            provider = OpenAiSubscriptionProvider(self.ctx, self.base)
            with self.assertRaises(SubscriptionError):
                await provider.chat({"model": "fixture-model", "messages": []}, {"user": "alice"})
            self.base.chat.assert_not_awaited()
        self.assertEqual(self.inference, [])
        query = parse_qs(urlsplit(self.connect()["auth_url"]).query)
        self.assertEqual(query["client_id"], ["dynamic_agent_client"])

    async def test_subscription_disables_api_key_for_all_modalities_until_disconnect(self):
        self.save_grant("admin")
        provider = OpenAiSubscriptionProvider(self.ctx, self.base)
        self.assertEqual(
            await provider.chat({"model": "fixture-model", "messages": []}, {"user": "alice"}), {"fallback": True}
        )
        self.save_grant()
        self.base.chat.reset_mock()
        for modality in ("image", "audio"):
            chat = {"model": "fixture-model", "messages": [], "modalities": ["text", modality]}
            with self.assertRaisesRegex(SubscriptionError, "OpenAI API key is disabled") as error:
                await provider.chat(chat, {"user": "alice"})
            self.assertFalse(error.exception.retryable)
            self.base.chat.assert_not_awaited()
        await provider.chat({"model": "fixture-model", "messages": []}, {"user": "alice"})
        self.base.chat.assert_not_awaited()
        models = [{"id": "api-only", "provider": "openai"}, {"id": "other", "provider": "other"}]
        self.assertEqual(
            [row["id"] for row in await self.auth.filter_models(models, "alice", api_available=True)],
            ["other", "fixture-model"],
        )
        self.auth.disconnect("alice")
        self.assertEqual(await provider.chat(chat, {"user": "alice"}), {"fallback": True})
        self.assertEqual(await self.auth.filter_models(models, "alice", api_available=True), models)
        self.assertEqual(self.base.api_key, "sk-fixture")
        self.assertIsNotNone(self.auth.store.load("admin"))
        self.assertEqual(provider.headers, {})

    async def test_provider_reload_and_unknown_model(self):
        await load(self.ctx)
        first = main.g_handlers["openai"]
        await reload_providers_hook(self.ctx)
        self.assertIs(main.g_handlers["openai"], first)
        main.g_handlers["openai"] = self.base
        await reload_providers_hook(self.ctx)
        self.assertIsInstance(main.g_handlers["openai"], OpenAiSubscriptionProvider)
        self.save_grant()
        with self.assertRaises(SubscriptionError):
            await main.g_handlers["openai"].chat({"model": "unknown-model", "messages": []}, {"user": "alice"})
        self.assertEqual(self.inference, [])

    async def test_cross_origin_settings_and_malformed_manual_body_rejected(self):
        response = await self.client.post(
            "/connect", headers={"Origin": "https://evil.test", "Test-User": "alice"}, json={}
        )
        self.assertEqual(response.status, 400)
        for body in ("[]", '{"url_or_code":123}', '{"url_or_code":"a","url_or_code":"b"}', "x" * 33000):
            response = await self.client.post("/callback_manual", data=body, headers={"Test-User": "alice"})
            self.assertEqual(response.status, 400)
        self.assertEqual(self.calls, [])

    async def test_http_limits_no_redirects_and_nonfinite_json(self):
        with self.assertRaises(SubscriptionError):
            await self.auth.http.json("GET", self.options.issuer + "/redirect")
        for raw in ('{"exp":NaN}', '{"exp":1e999}', '{"exp":1,"exp":2}', "[]"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_object(raw)
        for response in (
            {"access_token": "bad\r\ntoken"},
            {"expires_in": True},
            {"expires_in": 0},
            {"scope": []},
            {"token_type": 12},
        ):
            with self.subTest(response=response), self.assertRaises(SubscriptionError):
                self.auth.credentials(self.token_response(**response), "oaiapp_fixture", self.claims())

    async def test_cancelled_and_shutdown_flows_do_not_save(self):
        self.connect()
        self.delay_token = True
        task = asyncio.create_task(self.auth.callback("alice", self.callback_url()))
        await self.delay_entered.wait()
        self.auth.close()
        self.delay_release.set()
        with self.assertRaises(SubscriptionError):
            await task
        self.assertIsNone(self.auth.store.load("alice"))

    async def test_bounded_http_and_streamed_manual_request(self):
        self.connect()
        self.token_body_override = {"oversized": "x" * (1024 * 1024)}
        with self.assertRaises(SubscriptionError):
            await self.auth.callback("alice", self.callback_url())
        self.assertIsNone(self.auth.store.load("alice"))
        self.connect()

        async def body():
            yield b'{"url_or_code":"'
            await asyncio.sleep(0)
            yield b"x" * 33000
            yield b'"}'

        response = await self.client.post("/callback_manual", data=body(), headers={"Test-User": "alice"})
        self.assertEqual(response.status, 400)
        self.assertEqual(len(self.calls), 1)

    async def test_callback_unicode_state_is_rejected_without_exchange(self):
        self.connect()
        with self.assertRaises(SubscriptionError):
            await self.auth.callback("alice", self.callback_url(state="伪造"))
        self.assertEqual(self.calls, [])

    async def test_catalog_cannot_survive_account_change_during_response(self):
        self.save_grant()
        original = self.auth.http.json

        async def replace_after_response(method, url, **kwargs):
            value = await original(method, url, **kwargs)
            self.auth.disconnect("alice")
            return value

        with patch.object(self.auth.http, "json", replace_after_response), self.assertRaises(SubscriptionError):
            await self.auth.models("alice")
        self.assertNotIn("alice", self.auth.catalogs)

    async def test_stream_multiline_data_cancellation_and_no_store(self):
        provider = OpenAiSubscriptionProvider(self.ctx, self.base)

        async def lines():
            for line in (
                b": comment\n",
                b"event: response.output_text.delta\n",
                b'data: {"type":"response.output_text.delta",\n',
                b'data: "delta":"hello"}\n',
                b"\n",
                b'data:{"type":"response.completed","response":{"status":"completed"}}\n',
                b"\n",
            ):
                yield line

        writer = SimpleNamespace(write=AsyncMock(), flush=AsyncMock())
        response = SimpleNamespace(status=200, content=lines())
        result = await provider._process_responses_stream(response, {"model": "fixture-model"}, time.time(), {}, writer)
        self.assertEqual(result["choices"][0]["message"]["content"], "hello")
        writer.write.assert_any_await(
            {
                "role": "assistant",
                "content": "hello",
                "model": "fixture-model",
                "timestamp": writer.write.call_args.args[0]["timestamp"],
            },
            final=True,
        )
        response.content = lines()
        writer = SimpleNamespace(write=AsyncMock(), flush=AsyncMock())
        with (
            patch("llms.extensions.openai_auth.should_cancel_thread", return_value=True),
            self.assertRaises(asyncio.CancelledError),
        ):
            await provider._process_responses_stream(response, {"model": "fixture-model"}, time.time(), {}, writer)
        writer.flush.assert_awaited_once()
        self.save_grant()
        with patch.object(provider, "stream_writer", side_effect=AssertionError("no-store checkpoint")):
            await provider.chat({"model": "fixture-model", "messages": []}, {"user": "alice", "nostore": True})

    async def test_bootstrap_catalog_remains_available_for_legacy_and_expired_grants(self):
        models = [{"id": "fixture-model", "provider": "openai"}, {"id": "other-model", "provider": "other"}]
        other = [models[1]]
        self.assertEqual(await self.auth.filter_models(models, "alice"), other)
        self.assertEqual(await self.auth.filter_models(models, "alice", api_available=True), models)
        self.auth.store.save("alice", {"access_token": "legacy"})
        self.assertEqual(await self.auth.filter_models(models, "alice", api_available=True), other)
        self.save_grant(expires_at=time.time() - 1, refresh_token=None)
        self.assertEqual(await self.auth.filter_models(models, "alice", api_available=True), other)
        self.save_grant()
        catalog = await self.auth.filter_models(models, "alice")
        self.assertEqual([r["id"] for r in catalog], ["other-model", "fixture-model"])
        self.assertEqual(models[0], {"id": "fixture-model", "provider": "openai"})

    async def test_slow_failed_refresh_does_not_return_a_now_expired_token(self):
        now = [time.time()]
        self.save_grant(expires_at=now[0] + 5)

        async def fail_after_expiry(form):
            now[0] += 10
            raise SubscriptionError("fixture transport failure")

        with (
            patch("llms.extensions.openai_auth.security.time.time", side_effect=lambda: now[0]),
            patch.object(self.auth.http, "token", fail_after_expiry),
            self.assertRaises(SubscriptionError),
        ):
            await self.auth.valid_credentials("alice")

    async def automatic_connect(self, user="alice", **body):
        response = await self.client.post("/connect", headers={"Test-User": user}, json=body)
        self.assertEqual(response.status, 200, await response.text())
        result = await response.json()
        if not hasattr(self, "nonces"):
            self.nonces = {}
        self.nonces[user] = self.auth.pending[user]["nonce"]
        self.assertTrue(result["automatic_callback"])
        self.assertFalse(result["manual_callback"])
        return result

    async def test_automatic_callback_completes_origin_identity_without_manual_entry(self):
        self.options.redirect_uri = "http://127.0.0.1:0/auth/callback"
        target = str(self.client.make_url("/settings"))
        result = await self.automatic_connect(return_url=target)
        self.assertGreater(urlsplit(result["redirect_uri"]).port, 0)
        url = self.callback_url() + "&user=bob"
        async with self.client.session.get(url) as response:
            self.assertEqual(response.status, 200, await response.text())
            text = await response.text()
            self.assertIn("Connected to ChatGPT", text)
            self.assertIn("window.close()", text)
            self.assertIn(target, text)
            self.assertNotIn("access-alice", text)
            self.assertNotIn("state=", text)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.calls[0]["redirect_uri"], result["redirect_uri"])
        self.assertEqual(self.auth.store.load("alice")["subject"], "alice")
        self.assertIsNone(self.auth.store.load("bob"))
        self.assertIsInstance(main.g_handlers["openai"], OpenAiSubscriptionProvider)
        status = await (await self.client.get("/status", headers={"Test-User": "alice"})).json()
        self.assertTrue(status["connected"])
        self.assertFalse(status["pending"])
        self.assertTrue(status["automatic_callback"])
        async with self.client.session.get(url) as replay:
            self.assertEqual(replay.status, 400)
        self.assertEqual(len(self.calls), 1)

    async def test_automatic_callback_preserves_concurrent_user_ownership(self):
        self.options.redirect_uri = "http://127.0.0.1:0/auth/callback"
        await self.automatic_connect("alice")
        await self.automatic_connect("bob")
        urls = [self.callback_url(user) for user in ("alice", "bob")]
        responses = await asyncio.gather(*(self.client.session.get(url) for url in urls))
        for response in responses:
            self.assertEqual(response.status, 200, await response.text())
            response.release()
        for user in ("alice", "bob"):
            self.assertEqual(self.auth.store.load(user)["subject"], user)
        self.assertEqual(len(self.calls), 2)

    async def test_callback_port_conflict_uses_available_port_and_cleanup_releases_it(self):
        import socket

        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            self.options.redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
            result = await self.automatic_connect()
            selected = urlsplit(result["redirect_uri"]).port
            self.assertNotEqual(selected, port)
            self.assertGreater(selected, 0)
            await self.auth.cleanup()
            with socket.socket() as replacement:
                replacement.bind(("127.0.0.1", selected))
                replacement.listen()

    async def test_automatic_callback_rejects_state_replay_expiry_and_superseded_attempts(self):
        self.options.redirect_uri = "http://127.0.0.1:0/auth/callback"
        for kind in ("state", "duplicate", "expired", "disconnect", "new-login"):
            with self.subTest(kind=kind):
                await self.automatic_connect()
                url = self.callback_url()
                if kind == "state":
                    url = self.callback_url(state="wrong")
                elif kind == "duplicate":
                    url += "&state=second"
                elif kind == "expired":
                    self.auth.pending["alice"]["created_at"] -= self.options.flow_lifetime + 1
                elif kind == "disconnect":
                    self.auth.disconnect("alice")
                elif kind == "new-login":
                    await self.automatic_connect()
                async with self.client.session.get(url) as response:
                    self.assertEqual(response.status, 400)
                self.assertEqual(self.calls, [])
                self.assertIsNone(self.auth.store.load("alice"))

    async def test_callback_denial_is_reported_only_to_origin_user_and_cleared_on_retry(self):
        self.options.redirect_uri = "http://127.0.0.1:0/auth/callback"
        await self.automatic_connect()
        flow = self.auth.pending["alice"]
        url = flow["redirect"] + "?" + urlencode({"state": flow["state"], "error": "access_denied"})
        async with self.client.session.get(url) as response:
            self.assertEqual(response.status, 400)
            self.assertIn("not authorized", await response.text())
        for user in ("alice", "bob"):
            status = await (await self.client.get("/status", headers={"Test-User": user})).json()
            if user == "alice":
                self.assertIn("not authorized", status["callback_error"])
                self.assertFalse(status["pending"])
            else:
                self.assertIsNone(status["callback_error"])
        await self.automatic_connect()
        self.assertIsNone(self.auth.callback_error("alice"))
        self.assertEqual(self.calls, [])

    async def test_automatic_sign_in_rejects_untrusted_return_urls_before_starting_listener(self):
        for target in ("https://evil.test/", "/relative", 42, "http://localhost:1455/", "x" * 9000):
            with self.subTest(target=str(target)[:40]):
                response = await self.client.post(
                    "/connect", headers={"Test-User": "alice"}, json={"return_url": target}
                )
                self.assertEqual(response.status, 400)
        self.assertIsNone(self.auth.callback_receiver.runner)
        self.assertFalse(self.auth.has_pending("alice"))

    async def test_manual_completion_requires_explicit_host_opt_out(self):
        self.options.automatic_callback = False
        response = await self.client.post("/connect", headers={"Test-User": "alice"}, json={})
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertTrue(result["manual_callback"])
        self.assertFalse(result["automatic_callback"])
        self.assertIsNone(self.auth.callback_receiver.runner)
