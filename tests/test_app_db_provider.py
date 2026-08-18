import os
import json
import shutil
import sqlite3
import tempfile
import unittest

from llms.extensions.app import install, resolve_events_config


class EventsConfigTests(unittest.TestCase):
    def test_missing_events_config_uses_recommended_defaults(self):
        config = resolve_events_config({"defaults": {}})
        self.assertEqual(config["transport"], "auto")
        self.assertEqual(config["longPollTimeoutSeconds"], 25)
        self.assertEqual(config["sseHeartbeatSeconds"], 15)
        self.assertEqual(config["sseConnectTimeoutSeconds"], 5)
        self.assertEqual(config["sseFailureThreshold"], 3)
        self.assertEqual(config["sseRetryDelaySeconds"], 10)

    def test_events_config_accepts_transport_override_and_sanitizes_values(self):
        config = resolve_events_config({"defaults": {"events": {
            "transport": "long-poll",
            "longPollTimeoutSeconds": 40,
            "sseFailureThreshold": 0,
        }}})
        self.assertEqual(config["transport"], "long-poll")
        self.assertEqual(config["longPollTimeoutSeconds"], 40)
        self.assertEqual(config["sseFailureThreshold"], 1)

    def test_invalid_transport_safely_falls_back_to_auto(self):
        config = resolve_events_config({"defaults": {"events": {"transport": "websocket"}}})
        self.assertEqual(config["transport"], "auto")


class MockContext:
    def __init__(self, db_path):
        self.db_path = db_path
        self.chat_request_filters = []
        self.chat_response_filters = []
        self.chat_tool_filters = []
        self.config = {"database": db_path, "defaults": {}}
        self.debug = True
        self.threads = None
        self.compaction_calls = []

    def get_config(self):
        return self.config

    def get_home_path(self, name=""):
        return self.db_path

    def register_chat_request_filter(self, fn):
        self.chat_request_filters.append(fn)

    def register_chat_response_filter(self, fn):
        self.chat_response_filters.append(fn)

    def register_chat_tool_filter(self, fn):
        self.chat_tool_filters.append(fn)

    def register_chat_status_filter(self, fn):
        pass

    def register_chat_error_filter(self, fn):
        pass

    def get_username(self, req):
        return "test_user"

    def chat_to_system_prompt(self, chat):
        return "System prompt"

    def last_user_prompt(self, chat):
        return "Hello"

    def next_loading_message(self):
        return "Loading..."

    def cache_message_inline_data(self, msg, context=None):
        pass

    def dbg(self, msg):
        pass

    def err(self, msg, err):
        pass

    def log(self, msg):
        pass

    def add_get(self, *args, **kwargs):
        pass

    def add_post(self, *args, **kwargs):
        pass

    def add_delete(self, *args, **kwargs):
        pass

    def add_put(self, *args, **kwargs):
        pass

    def add_patch(self, *args, **kwargs):
        pass

    def add_importmaps(self, *args, **kwargs):
        pass

    def add_index_header(self, *args, **kwargs):
        pass

    def add_index_footer(self, *args, **kwargs):
        pass

    def get_user_path(self, user=None):
        return os.path.dirname(self.db_path)

    def chat_response_to_message(self, response):
        return {"role": "assistant", "content": "Hi!"}

    async def chat_completion(self, chat, context=None):
        self.compaction_calls.append((chat, context))
        return {"choices": [{"message": {"content": json.dumps({
            "messages": [{"role": "assistant", "content": "Continuation-safe summary"}]
        })}}]}

    def parse_json_response(self, text):
        return json.loads(text)

class TestAppDbProvider(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.ctx = MockContext(self.db_path)

        # We need to mock g_db global in the extensions.app module
        # So we import and override it.
        import llms.extensions.app as app_mod
        self.original_g_db = app_mod.g_db

        # Let install create the AppDB instance
        install(self.ctx)
        self.app_db = app_mod.g_db

    async def test_shared_compaction_is_chunked_and_preserves_instructions_and_recent_tail(self):
        self.ctx.config["defaults"]["compact"] = {
            "model": "compact-model",
            "messages": [
                {"role": "system", "content": "Summarize historical data"},
                {"role": "user", "content": "{message_count} {token_count} {target_tokens} {messages_json}"},
            ],
        }
        system = {"role": "system", "content": "Authoritative instruction"}
        history = [system] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": str(i) + ("x" * 12000)}
            for i in range(12)
        ]

        compacted, _, _ = await self.ctx.compact_messages(
            history, target_tokens=20000, chunk_tokens=8000, user="test_user", recent_count=4
        )

        self.assertEqual(compacted[0], system)
        self.assertEqual([x["content"] for x in compacted[-4:]], [x["content"] for x in history[-4:]])
        self.assertGreater(len(self.ctx.compaction_calls), 1)
        self.assertTrue(all(call[1]["nohistory"] and call[1]["nostore"] for call in self.ctx.compaction_calls))
        self.assertEqual(len(compacted[:-4]), 2)  # protected system + one consolidated summary
        self.assertEqual(compacted[1]["role"], "system")

    async def test_projected_tool_checkpoint_appends_only_new_canonical_messages(self):
        original = [{"role": "user", "content": "Do the work", "timestamp": 1}]
        thread_id = await self.app_db.create_thread_async(
            {"title": "projected", "messages": original}, user="test_user"
        )
        projected = [{"role": "assistant", "content": "Internal compacted summary"}]
        tool_call = {
            "role": "assistant", "content": "", "timestamp": 2,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "test", "arguments": "{}"}}],
        }
        tool_result = {"role": "tool", "tool_call_id": "call-1", "content": "done", "timestamp": 3}
        context = {
            "threadId": thread_id, "user": "test_user", "projectedContext": True,
            "projectedPersistedCount": 1, "runId": None, "stepId": None,
        }

        for filter_fn in self.ctx.chat_tool_filters:
            await filter_fn({"messages": projected + [tool_call, tool_result]}, context)

        stored = json.loads(self.app_db.get_thread(thread_id, user="test_user")["messages"])
        self.assertEqual([x["role"] for x in stored], ["user", "assistant", "tool"])
        self.assertFalse(any(x.get("content") == "Internal compacted summary" for x in stored))

    async def test_projected_checkpoint_survives_provider_sequence_normalization(self):
        original = [{"role": "user", "content": "Do the work", "timestamp": 1}]
        thread_id = await self.app_db.create_thread_async(
            {"title": "normalized projection", "messages": original}, user="test_user"
        )
        projected = [
            {"role": "assistant", "content": "summary one", "timestamp": 10},
            {"role": "assistant", "content": "summary two", "timestamp": 11},
        ]
        context = {
            "threadId": thread_id, "user": "test_user", "projectedContext": True,
            "projectedPersistedCount": len(projected), "runId": None, "stepId": None,
        }
        for filter_fn in self.ctx.chat_request_filters:
            await filter_fn({"messages": projected}, context)

        # Simulate GLM merging two projected messages before producing a tool call.
        tool_call = {
            "role": "assistant", "content": "", "timestamp": 12,
            "tool_calls": [{"id": "call-1", "type": "function",
                            "function": {"name": "test", "arguments": "{}"}}],
        }
        tool_result = {
            # Tool results are created by the agent loop without a timestamp; the
            # persistence filter must assign one before identity reconciliation.
            "role": "tool", "tool_call_id": "call-1", "content": "done",
        }
        normalized = [{"role": "system", "content": "summary one\n\nsummary two", "timestamp": 10}]
        for filter_fn in self.ctx.chat_tool_filters:
            await filter_fn({"messages": normalized + [tool_call]}, context)
            await filter_fn({"messages": normalized + [tool_call, tool_result]}, context)

        stored = json.loads(self.app_db.get_thread(thread_id, user="test_user")["messages"])
        self.assertEqual([x["role"] for x in stored], ["user", "assistant", "tool"])
        self.assertEqual(stored[-1]["tool_call_id"], "call-1")
        self.assertIsInstance(stored[-1].get("timestamp"), int)

    async def test_message_window_loads_head_tail_and_bidirectional_pages(self):
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}", "timestamp": i + 1}
            for i in range(150)
        ]
        thread_id = await self.app_db.create_thread_async(
            {"title": "long thread", "messages": messages}, user="test_user"
        )

        window = self.app_db.get_chat_message_window(thread_id, head=20, tail=100)
        self.assertEqual(len(window), 120)
        self.assertEqual([x["_sequence"] for x in window[:20]], list(range(1, 21)))
        self.assertEqual([x["_sequence"] for x in window[20:]], list(range(51, 151)))

        forward = self.app_db.get_chat_message_page(thread_id, after=20, take=100)
        backward = self.app_db.get_chat_message_page(thread_id, before=51, take=100)
        self.assertEqual([x["_sequence"] for x in forward], list(range(21, 121)))
        self.assertEqual([x["_sequence"] for x in backward], list(range(1, 51)))
        self.assertEqual(self.app_db.get_chat_message_bounds(thread_id)["messageCount"], 150)

    def tearDown(self):
        self.app_db.close()
        import llms.extensions.app as app_mod
        app_mod.g_db = self.original_g_db
        shutil.rmtree(self.temp_dir)

    async def test_chat_filters_save_and_update_provider(self):
        chat = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hello"}]
        }
        context = {
            "chat": chat,
            "user": "test_user",
            "provider": "test-provider",
            "modelInfo": {"id": "test-model", "name": "Test Model", "cost": {"input": 0, "output": 0}}
        }

        # 1. Run the request filter (simulating start of chat)
        for filter_fn in self.ctx.chat_request_filters:
            await filter_fn(chat, context)

        thread_id = context.get("threadId")
        self.assertIsNotNone(thread_id)

        # Verify thread was created with provider
        thread = self.app_db.get_thread(thread_id, user="test_user")
        self.assertIsNotNone(thread)
        self.assertEqual(thread.get("provider"), "test-provider")
        self.assertEqual(thread.get("model"), "test-model")

        # 2. Run the response filter (simulating completion of chat with a different provider if retried/fallback)
        context["provider"] = "fallback-provider"
        response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "Hi!"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}
        }
        for filter_fn in self.ctx.chat_response_filters:
            await filter_fn(response, context)

        # Verify thread was updated with the actual completing provider
        updated_thread = self.app_db.get_thread(thread_id, user="test_user")
        self.assertEqual(updated_thread.get("provider"), "fallback-provider")

    async def test_normalized_messages_are_append_only_and_idempotent(self):
        messages = [
            {"role": "user", "content": "one", "timestamp": 1001},
            {"role": "assistant", "content": "two", "timestamp": 1002},
        ]
        thread_id = await self.app_db.create_thread_async(
            {"model": "test-model", "messages": messages}, user="test_user"
        )
        rows = self.app_db.get_chat_messages(thread_id)
        self.assertEqual([x["message"]["content"] for x in rows], ["one", "two"])

        await self.app_db.update_thread_async(
            thread_id,
            {"messages": messages + [{"role": "tool", "content": "three", "timestamp": 1003}]},
            user="test_user",
        )
        await self.app_db.update_thread_async(
            thread_id,
            {"messages": messages + [{"role": "tool", "content": "three", "timestamp": 1003}]},
            user="test_user",
        )
        rows = self.app_db.get_chat_messages(thread_id)
        self.assertEqual([x["sequence"] for x in rows], [1, 2, 3])

    async def test_agent_run_and_steps_are_durable(self):
        thread_id = await self.app_db.create_thread_async(
            {"model": "test-model", "messages": [{"role": "user", "content": "work"}]},
            user="test_user",
        )
        run_id = self.app_db.create_agent_run(thread_id, "test_user", "test-model", max_steps=25)
        step_id = self.app_db.create_agent_step(
            run_id, 1, "model", idempotencyKey=f"run:{run_id}:step:1"
        )
        self.app_db.update_agent_step(step_id, {"status": "completed", "output": {"yielded": True}})
        self.app_db.update_agent_run(run_id, {"status": "queued", "stepCount": 1, "sliceCount": 1})

        run = self.app_db.get_active_agent_run(thread_id, user="test_user")
        self.assertEqual(run["id"], run_id)
        self.assertEqual(run["stepCount"], 1)
        steps = self.app_db.get_agent_steps(run_id)
        self.assertEqual(steps[0]["output"], {"yielded": True})

    async def test_agent_runs_are_atomically_claimed_and_recovered(self):
        thread_id = await self.app_db.create_thread_async(
            {"model": "test-model", "messages": [{"role": "user", "content": "work"}]},
            user="test_user",
        )
        first = self.app_db.create_agent_run(thread_id, "test_user", "test-model")
        claimed = self.app_db.claim_agent_runs("worker-1", limit=1, lease_seconds=60)
        self.assertEqual([run["id"] for run in claimed], [first])
        self.assertEqual(claimed[0]["status"], "running")
        self.assertEqual(claimed[0]["leaseOwner"], "worker-1")
        self.assertEqual(self.app_db.claim_agent_runs("worker-2", limit=1), [])

        self.assertEqual(self.app_db.requeue_interrupted_agent_runs(), 1)
        reclaimed = self.app_db.claim_agent_runs("worker-2", limit=1)
        self.assertEqual([run["id"] for run in reclaimed], [first])

    async def test_shutdown_preserves_threads_with_resumable_agent_runs(self):
        thread_id = await self.app_db.create_thread_async(
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "work"}],
                "streamingMessage": {"role": "assistant", "content": "partial"},
            },
            user="test_user",
        )
        run_id = self.app_db.create_agent_run(thread_id, "test_user", "test-model")
        self.app_db.update_agent_run(run_id, {"stepCount": 1})
        db_file = self.app_db.db_path

        self.app_db.close()
        with sqlite3.connect(db_file) as conn:
            conn.row_factory = sqlite3.Row
            thread = dict(conn.execute("SELECT * FROM thread WHERE id=?", (thread_id,)).fetchone())
            run = dict(conn.execute("SELECT * FROM agent_run WHERE id=?", (run_id,)).fetchone())

        self.assertIsNone(thread["completedAt"])
        self.assertIsNone(thread["error"])
        self.assertIsNone(thread["streamingMessage"])
        self.assertEqual(thread["status"], "Continuing…")
        self.assertEqual(run["status"], "queued")

if __name__ == "__main__":
    unittest.main()
