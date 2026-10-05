import copy
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from aiohttp import web

from llms.extensions.jev.client import normalize_answers
from llms.extensions.jev.schema import ValidationError, compile_request, validate_recipe
from llms.extensions.jev.sharing import Sharing, execution_package, validate_execution
from llms.extensions.jev.storage import ConflictError, JevStore, RecipeExistsError, document_hash, read_json, write_json
from llms.extensions.share_llmspy.client import get_publish_config, reference_from_url
from tests.test_jev import sample_response, starter

FIXTURES = Path(__file__).parent / "fixtures/jev-sharing-contract.json"


class ContractTests(TestCase):
    def test_shared_fixture_corpus(self):
        for case in json.loads(FIXTURES.read_text()):
            with self.subTest(case=case["name"]):

                def validate(case=case):
                    from llms.extensions.jev.storage import recipe_filename

                    recipe_filename(case["filename"])
                    doc = validate_recipe(case["document"])
                    validate_execution(doc, case["execution"])

                if case["valid"]:
                    validate()
                else:
                    with self.assertRaises(ValueError):
                        validate()

    def test_publisher_account_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            ctx = SimpleNamespace(get_user_path=lambda user=None: str(Path(root) / (user or "default")))
            write_json(Path(root) / "default/publish/config.json", {"apiKey": "default-secret"})
            self.assertIsNone(get_publish_config(ctx, "alice", False)["apiKey"])
            write_json(Path(root) / "alice/publish/config.json", {"apiKey": "alice-secret"})
            self.assertEqual(get_publish_config(ctx, "alice", False)["apiKey"], "alice-secret")
            self.assertNotEqual(get_publish_config(ctx, "alice")["apiKey"], "alice-secret")
            self.assertEqual(get_publish_config(ctx, None, False)["apiKey"], "default-secret")

    def test_sharing_uses_the_publish_extensions_saved_account(self):
        from unittest.mock import AsyncMock, MagicMock

        from llms.extensions.share_llmspy import install

        with tempfile.TemporaryDirectory() as root:
            ctx = MagicMock()
            ctx.get_user_path = lambda user=None: str(Path(root) / "user" / (user or "default"))
            ctx.get_username.return_value = "admin"
            ctx.app = SimpleNamespace(publisher_available=False)
            install(ctx)
            save = next(args[0][1] for args in ctx.add_post.call_args_list if args[0][0] == "config.json")
            request = SimpleNamespace(
                json=AsyncMock(
                    return_value={
                        "apiKey": "admin-publisher-key",
                        "userId": "publisher-admin",
                        "userName": "published_admin",
                        "baseUrl": "https://publisher.example",
                    }
                )
            )
            import asyncio

            asyncio.run(save(request))
            client = Sharing(ctx).client("admin")
            self.assertEqual(client.config["apiKey"], "admin-publisher-key")
            self.assertEqual(client.config["userId"], "publisher-admin")
            self.assertEqual(client.base, "https://publisher.example")
            self.assertNotEqual(get_publish_config(ctx, "admin")["apiKey"], client.config["apiKey"])
            self.assertIsNone(get_publish_config(ctx, "another-user", False)["apiKey"])

    def test_link_boundary(self):
        config = {"baseUrl": "https://ai.llmspy.org"}
        for value in ("https://ai.llmspy.org/d/abc", "https://ai.llmspy.org/d/abc.json", "https://ai.llmspy.org/d/abc/recipe.json"):
            self.assertEqual(reference_from_url(config, value), "abc")
        for value in (
            "https://evil.example/d/abc",
            "https://ai.llmspy.org@evil.example/d/abc",
            "http://ai.llmspy.org/d/abc",
            "https://ai.llmspy.org/fetch?url=x",
            "https://ai.llmspy.org/d/../etc",
            "https://user@ai.llmspy.org/d/abc",
        ):
            with self.subTest(url=value), self.assertRaises(web.HTTPBadRequest):
                reference_from_url(config, value)


class Remote:
    def __init__(self):
        self.base = "https://ai.llmspy.org"
        self.config = {"baseUrl": self.base, "userId": "publisher", "apiKey": "secret"}
        self.rows = {}
        self.calls = []
        self.fail = False
        self.hook = None

    async def request(self, method, path, payload=None, authenticated=False):
        self.calls.append((method, path, copy.deepcopy(payload), authenticated))
        if self.hook:
            hook, self.hook = self.hook, None
            hook()
        ref = path.split("?")[0].split("/")[-1].removesuffix(".json")
        if method == "POST":
            key = payload["idempotencyKey"]
            row = next((r for r in self.rows.values() if r["key"] == key), None)
            if row is None:
                ref = "ref" + str(len(self.rows) + 1)
                row = {
                    **copy.deepcopy(payload),
                    "key": key,
                    "externalRef": ref,
                    "revision": 1,
                    "contentHash": "hash1",
                    "publishedUrl": self.base + "/d/" + ref,
                    "author": {"userName": "author"},
                }
                self.rows[ref] = row
            if self.fail:
                self.fail = False
                raise web.HTTPBadGateway(text="Lost response")
            return copy.deepcopy(row)
        if ref not in self.rows:
            raise web.HTTPNotFound()
        if method == "PUT":
            row = self.rows[ref]
            if row["revision"] != payload["revision"]:
                raise web.HTTPConflict()
            row.update(copy.deepcopy(payload))
            row["revision"] += 1
            row["contentHash"] = "hash" + str(row["revision"])
        if method == "DELETE":
            self.rows.pop(ref)
            return {}
        return copy.deepcopy(self.rows[ref]["document"] if path.startswith("/d/") else self.rows[ref])


class SharingTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = JevStore(self.temp.name)
        self.document = starter()
        self.row = self.db.save(self.document, filename="support.json")
        self.inputs = self.document["examples"][0]["input"]
        ctx = SimpleNamespace(
            app=SimpleNamespace(publisher_available=True), get_user_path=lambda user=None: self.temp.name
        )
        write_json(Path(self.temp.name) / "publish/config.json", {"userId": "publisher", "apiKey": "secret"})
        self.sharing = Sharing(ctx)
        self.remote = Remote()
        self.sharing.client = lambda user: self.remote

    def run_success(self, row=None):
        row = row or self.row
        doc = row["document"]
        if doc.get("examples"):
            inputs = doc["examples"][0]["input"]
        elif isinstance(row.get("sourceExample"), dict) and "input" in row["sourceExample"]:
            inputs = row["sourceExample"]["input"]
        else:
            inputs = self.inputs
        run, _ = self.db.submit(
            str(time.time_ns()), doc, inputs, compile_request(doc, inputs), "lease", row["id"], row["revision"]
        )
        raw = sample_response(doc)
        self.db.finish(run["id"], "succeeded", raw, normalize_answers(raw, doc["questions"]))
        return run["id"]

    def body(self, run, **kwargs):
        return {"revision": self.row["revision"], "runId": run, **kwargs}

    async def test_execution_required_and_no_provider_call(self):
        with self.assertRaises(ValidationError):
            await self.sharing.publish("alice", self.db, "support", self.body("missing"))
        run = self.run_success()
        body = self.body(run)
        self.document["description"] = "changed"
        self.db.save(self.document, "support", self.row["revision"])
        with self.assertRaises(ValidationError):
            await self.sharing.publish("alice", self.db, "support", body)
        self.assertEqual(self.remote.calls, [])

    async def test_save_actual_run_as_example_then_publish_without_rerunning(self):
        run_id = self.run_success()
        run = self.db.run(run_id)
        original_run = copy.deepcopy(run)
        package = execution_package(self.document, run)
        self.document["examples"].append(
            {
                "id": "recorded",
                "label": "Actual run",
                "input": copy.deepcopy(run["input"]),
                "provenance": "authored",
                "execution": package,
            }
        )
        self.row = self.db.save(self.document, self.row["id"], self.row["revision"])
        status = await self.sharing.status("alice", self.db, self.row["id"])
        self.assertEqual([r["id"] for r in status["runs"]], [run_id])
        shared = await self.sharing.publish("alice", self.db, self.row["id"], self.body(run_id))
        recorded = shared["document"]["examples"][-1]
        self.assertEqual(recorded["execution"]["answers"], original_run["answers"])
        self.assertEqual(recorded["execution"]["input"], original_run["input"])
        self.assertNotIn("expected", recorded)
        self.assertNotIn("response", recorded["execution"])
        self.assertEqual(self.db.run(run_id), original_run)

    async def test_usage_snapshot_counts_local_executions_and_retains_retry_capture(self):
        run = self.run_success()
        self.db.favourite(self.row["id"], True)
        other = self.db.save(self.document, filename="other.json")
        self.run_success(other)
        # A queued submission is not an execution yet.
        self.db.submit(
            "pending-usage",
            self.document,
            self.inputs,
            compile_request(self.document, self.inputs),
            "lease",
            self.row["id"],
            self.row["revision"],
        )
        status = await self.sharing.status("alice", self.db, self.row["id"])
        self.assertEqual(status["usage"], {"publisherStarred": True, "publisherRunCount": 1})
        self.remote.fail = True
        with self.assertRaises(web.HTTPBadGateway):
            await self.sharing.publish("alice", self.db, self.row["id"], self.body(run))
        self.db.favourite(self.row["id"], False)
        self.run_success()
        receipt = await self.sharing.publish("alice", self.db, self.row["id"], self.body(run))
        self.assertTrue(receipt["publisherStarred"])
        self.assertEqual(receipt["publisherRunCount"], 1)
        status = await self.sharing.status("alice", self.db, self.row["id"])
        self.assertTrue(status["savedChangesNotShared"])
        updated = await self.sharing.publish(
            "alice", self.db, self.row["id"], self.body(run, publishedRevision=receipt["revision"])
        )
        self.assertFalse(updated["publisherStarred"])
        self.assertEqual(updated["publisherRunCount"], 2)
        self.assertEqual(updated["revision"], 2)
        self.assertFalse((await self.sharing.status("alice", self.db, self.row["id"]))["savedChangesNotShared"])

    async def test_tags_are_proxied_anonymously_to_the_configured_publisher(self):
        from unittest.mock import AsyncMock

        catalog = {"version": 3, "tags": [{"label": "Email", "group": "content"}]}
        self.remote.request = AsyncMock(return_value=catalog)
        self.assertEqual(await self.sharing.tags("alice"), catalog)
        self.remote.request.assert_awaited_once_with("GET", "/publish/decisions/tags")

    async def test_community_stars_use_the_connected_publish_account(self):
        from unittest.mock import AsyncMock

        state = {"externalRef": "ref1", "starCount": 2, "starred": True}
        self.remote.request = AsyncMock(return_value=state)
        self.assertEqual(await self.sharing.star("alice", "ref1", {"starred": True}), state)
        self.remote.request.assert_awaited_once_with(
            "PUT", "/publish/decision/ref1/star", {"starred": True}, authenticated=True
        )
        for body in ({}, {"starred": "yes"}, {"starred": 1}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                await self.sharing.star("alice", "ref1", body)

    async def test_catalog_uses_connected_account_for_personal_star_state(self):
        from unittest.mock import AsyncMock

        self.remote.request = AsyncMock(return_value={"items": []})
        await self.sharing.catalog("alice", {})
        self.remote.request.assert_awaited_once_with(
            "GET", "/publish/decisions?take=20&skip=0", authenticated=True
        )

    async def test_complete_recipe_documentation_and_one_allowlisted_execution(self):
        run = self.run_success()
        history_before = self.db.run(run)
        other_run = self.run_success()
        other_history_before = self.db.run(other_run)
        receipt = await self.sharing.publish("alice", self.db, "support", self.body(run))
        sent = self.remote.calls[0][2]
        self.assertEqual(sent["document"], self.document)
        self.assertEqual(self.db.recipe("support")["document"], self.document)
        self.assertEqual(
            set(sent["execution"]), {"status", "input", "prompt", "answers", "model", "completedAt", "durationMs"}
        )
        self.assertNotIn("response", sent["execution"])
        self.assertEqual(self.db.run(run), history_before)
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual((await self.sharing.status("alice", self.db, "support"))["publication"]["sourceRunId"], run)
        self.assertEqual(self.db.run(other_run), other_history_before)
        updated = await self.sharing.publish(
            "alice", self.db, "support", self.body(run, includeAdditionalExamples=False, publishedRevision=1)
        )
        self.assertEqual(updated["document"], self.document)
        self.assertEqual(self.db.run(run), history_before)

    async def test_legacy_pending_snapshot_recovers_unchanged_then_updates_with_examples(self):
        run = self.run_success()
        self.remote.fail = True
        with self.assertRaises(web.HTTPBadGateway):
            await self.sharing.publish("alice", self.db, "support", self.body(run))
        # Simulate a pre-upgrade journal and remote receipt that excluded authored examples.
        with self.db.transaction() as index:
            pending = index["recipes"]["support"]["pendingPublication"]
            payload = read_json(self.db.root / pending["pendingPayloadRef"])
            payload["document"].pop("examples")
            pending["includeAdditionalExamples"] = False
            pending["payloadHash"] = document_hash(payload)
            write_json(self.db.root / pending["pendingPayloadRef"], payload)
        self.remote.rows["ref1"]["document"].pop("examples")
        status = await self.sharing.status("alice", self.db, "support")
        self.assertEqual(status["pendingPublication"]["payload"], payload)
        receipt = await self.sharing.publish("alice", self.db, "support", self.body(run))
        self.assertNotIn("examples", receipt["document"])
        self.assertTrue((await self.sharing.status("alice", self.db, "support"))["savedChangesNotShared"])
        updated = await self.sharing.publish("alice", self.db, "support", self.body(run, publishedRevision=1))
        self.assertEqual(updated["document"], self.document)
        self.assertEqual(updated["externalRef"], receipt["externalRef"])
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(self.db.recipe("support")["document"], self.document)
        self.assertIsNotNone(self.db.run(run))
        self.assertFalse((await self.sharing.status("alice", self.db, "support"))["savedChangesNotShared"])

    async def test_pending_create_survives_lost_response_and_later_edit(self):
        run = self.run_success()
        self.remote.fail = True
        with self.assertRaises(web.HTTPBadGateway):
            await self.sharing.publish("alice", self.db, "support", self.body(run))
        pending = read_json(self.db.index_path)["recipes"]["support"]["pendingPublication"]
        self.assertTrue((self.db.root / pending["pendingPayloadRef"]).exists())
        status = await self.sharing.status("alice", self.db, "support")
        self.assertNotIn("idempotencyKey", status["pendingPublication"]["reservation"])
        self.assertNotIn("pendingPayloadRef", status["pendingPublication"]["reservation"])
        changed = copy.deepcopy(self.document)
        changed["description"] = "later edit"
        self.db.save(changed, "support", self.row["revision"])
        await self.sharing.publish("alice", self.db, "support", self.body(run))
        self.assertEqual(len(self.remote.rows), 1)
        self.assertEqual(self.db.recipe("support")["document"]["description"], "later edit")
        self.assertNotEqual(self.remote.rows["ref1"]["document"]["description"], "later edit")
        self.assertFalse((self.db.root / pending["pendingPayloadRef"]).exists())

    async def test_concurrent_delete_does_not_recreate_or_bind_receipt(self):
        run = self.run_success()
        self.remote.hook = lambda: self.db.delete_recipe("support")
        receipt = await self.sharing.publish("alice", self.db, "support", self.body(run))
        self.assertIsNone(self.db.recipe("support"))
        self.assertIn(receipt["externalRef"], self.remote.rows)

    async def test_import_pin_replacement_and_published_example(self):
        run = self.run_success()
        receipt = await self.sharing.publish("alice", self.db, "support", self.body(run))
        body = {
            "externalRef": receipt["externalRef"],
            "publishedRevision": 1,
            "contentHash": "hash1",
            "filename": "support.json",
        }
        with self.assertRaises(RecipeExistsError):
            await self.sharing.import_recipe("alice", self.db, body)
        self.assertIsNotNone(self.db.run(run))
        with self.assertRaises(ConflictError):
            await self.sharing.import_recipe("alice", self.db, {**body, "contentHash": "changed"})
        row = await self.sharing.import_recipe("alice", self.db, {**body, "replaceRevision": self.row["revision"]})
        self.assertIsNone(self.db.run(run))
        self.assertNotIn("publication", row)
        self.assertEqual(row["sourceExample"]["status"], "succeeded")
        self.assertEqual((await self.sharing.status("alice", self.db, "support"))["runs"], [])
        self.assertEqual(self.db.recipe("support")["sourceExample"], row["sourceExample"])
        self.assertTrue(all(not auth for method, _, _, auth in self.remote.calls if method == "GET"))

    async def test_update_conflict_clears_reservation_for_review(self):
        run = self.run_success()
        await self.sharing.publish("alice", self.db, "support", self.body(run))
        self.remote.rows["ref1"]["revision"] = 2
        self.remote.rows["ref1"]["document"]["description"] = "remote edit"
        with self.assertRaises(web.HTTPConflict):
            await self.sharing.publish("alice", self.db, "support", self.body(run, publishedRevision=1))
        meta = read_json(self.db.index_path)["recipes"]["support"]
        self.assertNotIn("pendingPublication", meta)
        self.assertEqual(meta["publication"]["publicRevision"], 2)
        for _ in range(2):
            status = await self.sharing.status("alice", self.db, "support")
            self.assertTrue(status["savedChangesNotShared"])
            self.assertTrue(status["publication"]["remoteChanged"])

    async def test_another_account_cannot_reuse_publication_binding(self):
        run = self.run_success()
        await self.sharing.publish("alice", self.db, "support", self.body(run))
        self.remote.config["userId"] = "different"
        await self.sharing.publish("alice", self.db, "support", self.body(run))
        self.assertEqual(len(self.remote.rows), 2)
        self.assertEqual(self.remote.calls[-1][0], "POST")

    async def test_non_successful_other_filename_and_changed_document_are_ineligible(self):
        for status in ("pending", "failed", "cancelled", "interrupted"):
            run, _ = self.db.submit(
                str(time.time_ns()),
                self.document,
                self.inputs,
                compile_request(self.document, self.inputs),
                "lease",
                "support",
                1,
            )
            if status != "pending":
                self.db.finish(run["id"], status)
            with self.assertRaises(ValidationError):
                await self.sharing.publish("alice", self.db, "support", self.body(run["id"]))
            if status == "pending":
                self.db.finish(run["id"], "cancelled")
        other = self.db.save(self.document, filename="another.json")
        run = self.run_success(other)
        with self.assertRaises(ValidationError):
            await self.sharing.publish("alice", self.db, "support", self.body(run))

    async def test_shared_import_journal_recovers_after_interruption(self):
        run = self.run_success()
        receipt = await self.sharing.publish("alice", self.db, "support", self.body(run))
        body = {
            "externalRef": receipt["externalRef"],
            "publishedRevision": 1,
            "contentHash": "hash1",
            "filename": "support.json",
            "replaceRevision": 1,
        }
        with (
            patch.object(self.db, "_apply_shared_import", side_effect=RuntimeError("process interruption")),
            self.assertRaises(RuntimeError),
        ):
            await self.sharing.import_recipe("alice", self.db, body)
        self.assertTrue((self.db.root / ".shared-import.json").exists())
        recovered = JevStore(self.temp.name)
        self.assertFalse((self.db.root / ".shared-import.json").exists())
        self.assertIsNone(recovered.run(run))
        row = recovered.recipe("support")
        self.assertNotIn("publication", row)
        self.assertIsNotNone(row["sourceExample"])
        next_run = self.run_success(row)
        self.assertTrue(next_run.endswith("00002"))

    async def test_published_example_retains_original_document_after_local_edits(self):
        run = self.run_success()
        receipt = await self.sharing.publish("alice", self.db, "support", self.body(run))
        row = await self.sharing.import_recipe(
            "alice",
            self.db,
            {
                "externalRef": receipt["externalRef"],
                "publishedRevision": 1,
                "contentHash": "hash1",
                "filename": "copy.json",
            },
        )
        original = copy.deepcopy(row["document"])
        edited = copy.deepcopy(original)
        edited["description"] = "An independent local edit"
        edited["questions"] = {"different": next(iter(edited["questions"].values()))}
        edited.pop("presentation", None)
        self.db.save(edited, row["id"], row["revision"])
        reloaded = self.db.recipe(row["id"])
        self.assertEqual(reloaded["sourceDocument"], original)
        self.assertEqual(reloaded["sourceExample"], row["sourceExample"])
        self.assertNotEqual(reloaded["document"]["questions"], reloaded["sourceDocument"]["questions"])

    async def test_source_example_is_cleared_with_local_replacement_and_deletion(self):
        run = self.run_success()
        receipt = await self.sharing.publish("alice", self.db, "support", self.body(run))
        row = await self.sharing.import_recipe(
            "alice",
            self.db,
            {
                "externalRef": receipt["externalRef"],
                "publishedRevision": 1,
                "contentHash": "hash1",
                "filename": "copy.json",
            },
        )
        sidecar = self.db.root / "source-examples" / row["importSource"]["exampleRef"]
        self.assertTrue(sidecar.exists())
        self.db.delete_recipe(row["id"])
        self.assertFalse(sidecar.exists())


class NetworkTests(IsolatedAsyncioTestCase):
    async def test_authenticated_requests_use_the_saved_publish_key(self):
        from aiohttp.test_utils import TestServer

        from llms.extensions.share_llmspy.client import PublisherClient

        seen = []

        async def handler(request):
            seen.append(request.headers.get("Authorization"))
            return web.json_response({"externalRef": "fixture"})

        app = web.Application()
        app.router.add_post("/publish/decision", handler)
        with tempfile.TemporaryDirectory() as root:
            ctx = SimpleNamespace(get_user_path=lambda user=None: str(Path(root) / "user" / (user or "default")))
            async with TestServer(app) as server:
                write_json(
                    Path(ctx.get_user_path("admin")) / "publish/config.json",
                    {"apiKey": "saved-admin-key", "baseUrl": str(server.make_url("")).rstrip("/"), "allowHttp": True},
                )
                write_json(Path(ctx.get_user_path()) / "publish/config.json", {"apiKey": "different-default-key"})
                client = PublisherClient(get_publish_config(ctx, "admin", False))
                await client.request("POST", "/publish/decision", {"fixture": True}, authenticated=True)
        self.assertEqual(seen, ["Bearer saved-admin-key"])

    async def test_anonymous_fetches_never_send_a_key_and_redirects_are_rejected(self):
        from aiohttp.test_utils import TestServer

        from llms.extensions.share_llmspy.client import PublisherClient

        headers = []

        async def handler(request):
            headers.append(request.headers.get("Authorization"))
            if request.path == "/redirect":
                raise web.HTTPFound(location="https://evil.example/private")
            if request.path == "/text":
                return web.Response(text="not JSON")
            return web.json_response({"items": []})

        app = web.Application()
        app.router.add_get("/{name}", handler)
        async with TestServer(app) as server:
            client = PublisherClient(
                {"baseUrl": str(server.make_url("")).rstrip("/"), "allowHttp": True, "apiKey": "never-forward"}
            )
            self.assertEqual(await client.request("GET", "/ok"), {"items": []})
            for path in ("/redirect", "/text"):
                with self.assertRaises(web.HTTPBadGateway):
                    await client.request("GET", path)
        self.assertEqual(headers, [None, None, None])
