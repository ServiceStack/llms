import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import llms.extensions.app as app
from llms.extensions.app.db import AppDB
from llms.extensions.app.messages import client_message, reconcile_client_messages
from tests.test_app_db_provider import MockContext


class Context(MockContext):
    def __init__(self, path):
        super().__init__(path)
        self.routes = {}
        self.request_args = {}
        self.config["defaults"]["summarize"] = None

    def add_get(self, path, fn):
        self.routes["GET", path] = fn

    def add_post(self, path, fn):
        self.routes["POST", path] = fn

    def add_patch(self, path, fn):
        self.routes["PATCH", path] = fn

    def is_admin(self, request):
        return False

    def check_auth(self, request):
        return True, None


def history():
    messages = [
        {"role": role, "content": f"Message {i}", "timestamp": i + 1}
        for i, role in enumerate(["user", "system", "assistant", "tool", "assistant", "user", "assistant"])
    ]
    signature = "opaque" * 12000
    for message in messages:
        if message["role"] == "assistant":
            message["provider_metadata"] = {
                "google": {
                    "content": {"role": "model", "parts": [{"text": message["content"], "thoughtSignature": signature}]}
                }
            }
    messages[2]["tool_calls"] = [
        {
            "id": "fetch-1",
            "type": "function",
            "thoughtSignature": signature,
            "extra_content": {"google": {"thought_signature": signature}},
            "function": {"name": "fetch_url", "arguments": "{}"},
        }
    ]
    messages[3]["tool_call_id"] = "fetch-1"
    messages[-1]["groundingMetadata"] = {"groundingChunks": [{"retrievedContext": {"uri": "document"}}]}
    return messages


class ThreadProjectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.ctx = Context(os.path.join(self.directory.name, "app.sqlite"))
        self.db = AppDB(self.ctx, self.ctx.db_path)
        self.old_db = app.g_db
        app.g_db = self.db
        app.install(self.ctx)
        self.messages = history()
        self.tid = self.db.create_thread({"title": "Test", "messages": self.messages}, "test_user")
        self.wake = patch.object(app.AgentScheduler, "wake").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        app.g_db = self.old_db
        self.db.close()
        self.directory.cleanup()

    async def call(self, method="GET", route="threads/{id}", body=None, **query):
        request = SimpleNamespace(
            match_info={"id": str(self.tid)},
            query=query,
            body_exists=True,
            json=AsyncMock(return_value=copy.deepcopy(body)),
        )
        response = await self.ctx.routes[method, route](request)
        return json.loads(response.text)

    def stored(self):
        return [row["message"] for row in self.db.get_chat_messages(self.tid)]

    async def test_short_signed_history_returns_latest_and_preserves_server_state(self):
        original = self.stored()
        dto = await self.call()
        self.assertEqual([m["_sequence"] for m in dto["messages"]], list(range(1, 8)))
        self.assertEqual(dto["messageCount"], 7)
        self.assertEqual(dto["messageWindow"]["ranges"], [{"from": 1, "to": 7}])
        self.assertLess(len(json.dumps(dto)), 15000)
        self.assertNotIn("opaque", json.dumps(dto))
        self.assertEqual(dto["messages"][-1]["groundingMetadata"], self.messages[-1]["groundingMetadata"])
        self.assertEqual(self.stored(), original)
        self.assertEqual(self.ctx.threads.get_thread(self.tid, "test_user")["messages"], original)

    async def test_full_page_preview_and_poll_project_replay_data(self):
        for dto in [
            await self.call(allMessages="true"),
            await self.call(route="threads/{id}/messages", take="100", maxBytes="65536"),
            await self.call(route="threads/{id}/updates", sig="stale"),
        ]:
            self.assertEqual(len(dto["messages"]), 7)
            self.assertNotIn("opaque", json.dumps(dto))
        previews = await self.call(route="threads")
        self.assertNotIn("opaque", json.dumps(previews))
        self.assertEqual(previews[0]["messages"][-1]["timestamp"], 7)

    async def test_visible_oversized_latest_tool_group_is_complete(self):
        messages = [
            {"role": "user", "content": "x" * 180000, "timestamp": 1},
            {"role": "assistant", "content": "middle" * 100000, "timestamp": 2},
            {
                "role": "assistant",
                "content": "x" * 400000,
                "timestamp": 3,
                "tool_calls": [{"id": "a", "function": {"name": "fetch_url", "arguments": "{}"}}],
            },
            {"role": "tool", "content": "result", "tool_call_id": "a", "timestamp": 4},
        ]
        self.db.update_thread(self.tid, {"messages": messages, "truncate": True}, "test_user")
        dto = await self.call()
        self.assertEqual([m["timestamp"] for m in dto["messages"]], [1, 3, 4])
        self.assertEqual(len({m["_sequence"] for m in dto["messages"]}), 3)
        self.assertEqual(len(dto["messageWindow"]["ranges"]), 2)

    async def test_partial_equal_count_followup_preserves_hidden_reply_and_signatures(self):
        original = self.stored()
        incoming = [client_message(m) for m in original[:6]]
        incoming.append({"role": "user", "content": "Next", "timestamp": 8})
        await self.call("POST", "threads/{id}/chat", {"messages": incoming, "submissionId": "next"})
        self.assertEqual(self.stored()[:7], original)
        self.assertEqual(self.stored()[-1]["content"], "Next")
        self.assertEqual(json.loads(self.db.get_thread(self.tid, "test_user")["messages"])[:7], original)

    async def test_patch_echo_and_full_edit_hydrate_surviving_signed_messages(self):
        original = self.stored()
        projected = (await self.call(allMessages="true"))["messages"]
        await self.call("PATCH", body={"messages": projected})
        self.assertEqual(json.loads(self.db.get_thread(self.tid, "test_user")["messages"]), original)
        projected[5]["content"] = "Edited user prompt"
        await self.call("PATCH", body={"messages": projected[:6], "truncate": True})
        stored = self.stored()
        self.assertEqual(stored[:5], original[:5])
        self.assertEqual(stored[5]["content"], "Edited user prompt")
        self.assertEqual(len(stored), 6)

    async def test_redo_from_partial_window_preserves_missing_prefix(self):
        original = self.stored()
        incoming = [client_message(original[0]), client_message(original[5])]
        incoming[-1]["content"] = "Edited"
        await self.call("POST", "threads/{id}/chat", {"messages": incoming, "truncate": True})
        self.assertEqual(self.stored()[:5], original[:5])
        self.assertEqual(self.stored()[-1]["content"], "Edited")
        self.assertEqual(len(self.stored()), 6)

    async def test_followup_and_restart_replay_exact_gemini_parts(self):
        from tests.test_google_tool_combination import PARTS, GoogleToolCombinationTests

        gemini = GoogleToolCombinationTests()
        gemini.setUp()
        assistant, _ = await gemini.request(gemini.chat(), PARTS)
        assistant["timestamp"] = 2
        messages = [
            {"role": "user", "content": "Search", "timestamp": 1},
            assistant,
            {"role": "tool", "tool_call_id": "fetch-1", "content": "ok", "timestamp": 3},
        ]
        self.db.update_thread(self.tid, {"messages": messages, "truncate": True}, "test_user")
        projected = (await self.call())["messages"]
        projected.append({"role": "user", "content": "Continue", "timestamp": 4})
        await self.call("POST", "threads/{id}/chat", {"messages": projected})
        self.db.close()
        self.db = AppDB(self.ctx, self.ctx.db_path)
        app.g_db = self.db
        self.assertEqual(self.stored()[1]["provider_metadata"], assistant["provider_metadata"])
        _, payload = await gemini.request(gemini.chat(messages=self.stored()))
        self.assertEqual(payload["contents"][1]["parts"], PARTS)

    async def test_streaming_and_sse_project_replay_state(self):
        streaming = copy.deepcopy(self.messages[-1])
        streaming["timestamp"] = 8
        await self.db.update_thread_async(self.tid, {"streamingMessage": streaming}, "test_user")
        dto = await self.call()
        self.assertTrue(dto["messages"][-1]["streaming"])
        self.assertNotIn("opaque", json.dumps(dto))
        self.assertEqual(json.loads(self.db.get_thread(self.tid, "test_user")["streamingMessage"]), streaming)
        await self.db.update_thread_async(self.tid, {"completedAt": datetime.now()}, "test_user")
        response = SimpleNamespace(prepare=AsyncMock(), write=AsyncMock(), write_eof=AsyncMock())
        request = SimpleNamespace(match_info={"id": str(self.tid)}, query={})
        with patch.object(app.web, "StreamResponse", return_value=response):
            await self.ctx.routes["GET", "threads/{id}/updates/stream"](request)
        event = response.write.call_args.args[0].decode()
        self.assertIn("event: connected", event)
        self.assertNotIn("opaque", event)
        self.assertIn('"timestamp":7', event)

    async def test_full_history_recovers_compatibility_projection_gap(self):
        with self.db.create_writer_connection() as connection:
            connection.execute("UPDATE thread SET messages=? WHERE id=?", (json.dumps(self.messages[:6]), self.tid))
            connection.commit()
        dto = await self.call(allMessages="true")
        self.assertEqual(len(dto["messages"]), 7)
        self.assertEqual(dto["messages"][-1]["timestamp"], 7)


class MessageReconciliationTests(unittest.TestCase):
    def test_projection_preserves_tool_content_media_and_vendor_extras(self):
        original = history()[2]
        original["tool_calls"][0]["extra_content"]["other"] = {"value": 1}
        original["content"] = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]
        projected = client_message(original)
        self.assertEqual(projected["content"], original["content"])
        self.assertEqual(projected["tool_calls"][0]["extra_content"], {"other": {"value": 1}})
        self.assertIn("provider_metadata", original)

    def test_explicit_delete_preserves_signed_survivors_and_edit_invalidates_replay(self):
        original = history()
        projected = [client_message(m) for m in original]
        result = reconcile_client_messages(original, projected[:5] + projected[6:], rewrite=True)
        self.assertEqual(result, original[:5] + original[6:])
        projected[2]["content"] = "Changed assistant"
        result = reconcile_client_messages(original, projected, rewrite=True)
        self.assertNotIn("provider_metadata", result[2])
        self.assertNotIn("thoughtSignature", result[2]["tool_calls"][0])
        self.assertEqual(result[4], original[4])

    def test_normal_echo_cannot_overwrite_existing_content_even_with_larger_count(self):
        original = history()
        incoming = [client_message(m) for m in original]
        incoming[2]["content"] = "stale"
        incoming.append({"role": "user", "content": "Next", "timestamp": 8})
        self.assertEqual(reconcile_client_messages(original, incoming)[:7], original)
