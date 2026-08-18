#!/usr/bin/env python3
"""
The durable conversation must survive anything that happens to a streaming response.

Two independent mechanisms are tested here:

1. `StreamCheckpointWriter` writes only the thread's `streamingMessage`, so the
   streaming path structurally cannot touch `messages`.
2. `AppDB.guard_messages` refuses to shrink `messages` unless the caller explicitly
   opted in with `truncate`, so no request - however malformed - can erase a thread.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["MOCK"] = "0"
os.environ["OPENROUTER_API_KEY"] = "test-api-key"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.extensions.app.db import AppDB
from llms.main import OpenAiCompatible, StreamCheckpointWriter, cli, g_handlers, get_app, get_client_timeout


def history_messages():
    """A thread with two completed turns plus the user prompt being answered."""
    return [
        {"role": "user", "content": "turn 1 question", "timestamp": 1},
        {"role": "assistant", "content": "turn 1 answer", "timestamp": 2, "model": "m", "usage": {"tokens": 5}},
        {"role": "user", "content": "turn 2 question", "timestamp": 3},
        {"role": "assistant", "content": "turn 2 answer", "timestamp": 4, "model": "m", "usage": {"tokens": 5}},
        {"role": "user", "content": "turn 3 question", "timestamp": 5},
    ]


class FakeThreadsApi:
    """Stands in for ThreadApi, recording which column each write touched."""

    def __init__(self, thread=None):
        self.thread = thread if thread is not None else {"id": "t1", "messages": []}
        self.checkpoints = []
        self.message_writes = []
        self.db = self

    def get_thread(self, thread_id, user=None):
        return self.thread

    def get_thread_column(self, thread_id, column, user=None):
        return self.thread.get(column)

    async def update_thread_async(self, id, updates, user=None):
        self.thread.update(updates)
        if "messages" in updates:
            self.message_writes.append(updates["messages"])
        return 1

    async def checkpoint_stream_async(self, id, message, user=None):
        self.thread["streamingMessage"] = message
        self.checkpoints.append(message)
        return 1


class DyingStreamReader:
    """Yields SSE lines then raises, simulating a provider dying mid-stream."""

    def __init__(self, lines, die_after, exc):
        self.lines = [ln.encode("utf-8") for ln in lines]
        self.die_after = die_after
        self.exc = exc
        self.idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.idx >= self.die_after:
            raise self.exc
        if self.idx >= len(self.lines):
            raise StopAsyncIteration
        line = self.lines[self.idx]
        self.idx += 1
        await asyncio.sleep(0)
        return line


class MockCtx:
    """Minimal ctx for exercising AppDB directly."""

    def __init__(self):
        self.debug = False
        self.errors = []

    def cache_message_inline_data(self, msg, context=None):
        pass

    def dbg(self, msg):
        pass

    def log(self, msg):
        pass

    def err(self, msg, e=None):
        self.errors.append(msg)


class TestStreamCheckpointWriter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = FakeThreadsApi({"id": "t1", "messages": history_messages()})

    def writer(self, interval=0.0):
        return StreamCheckpointWriter(self.api, "t1", user=None, interval=interval)

    async def test_never_writes_the_messages_column(self):
        """The whole point: streaming cannot reach the durable conversation."""
        writer = self.writer()
        for text in ["a", "ab", "abc"]:
            await writer.write({"role": "assistant", "content": text})
        await writer.write({"role": "assistant", "content": "abc"}, final=True)

        self.assertEqual(self.api.message_writes, [], "streaming must not write messages")
        self.assertTrue(self.api.checkpoints)
        self.assertEqual(self.api.thread["messages"], history_messages())

    async def test_throttles_to_the_checkpoint_interval(self):
        """The first chunk lands immediately so output appears at once, then throttles."""
        writer = self.writer(interval=60.0)
        self.assertTrue(await writer.write({"role": "assistant", "content": "a"}))
        for text in ["ab", "abc"]:
            self.assertFalse(await writer.write({"role": "assistant", "content": text}))
        self.assertEqual([c["content"] for c in self.api.checkpoints], ["a"])

    async def test_final_write_bypasses_the_interval(self):
        writer = self.writer(interval=60.0)
        await writer.write({"role": "assistant", "content": "partial"})
        self.assertTrue(await writer.write({"role": "assistant", "content": "complete"}, final=True))
        self.assertEqual(self.api.checkpoints[-1]["content"], "complete")

    async def test_flush_persists_chunks_still_only_in_memory(self):
        """A stream that dies between checkpoints still keeps what it produced."""
        writer = self.writer(interval=60.0)
        await writer.write({"role": "assistant", "content": "first"})  # lands immediately
        await writer.write({"role": "assistant", "content": "streamed so far"})  # throttled
        self.assertEqual([c["content"] for c in self.api.checkpoints], ["first"])

        self.assertTrue(await writer.flush())
        self.assertEqual(self.api.checkpoints[-1]["content"], "streamed so far")
        self.assertEqual(self.api.message_writes, [])

    async def test_flush_is_a_noop_when_nothing_is_pending(self):
        writer = self.writer()
        self.assertFalse(await writer.flush())
        await writer.write({"role": "assistant", "content": "x"})  # wrote through
        self.assertFalse(await writer.flush())

    async def test_empty_message_is_never_written(self):
        writer = self.writer()
        self.assertFalse(await writer.write({"role": "assistant", "content": ""}))
        self.assertFalse(await writer.write({"role": "assistant", "content": None}, final=True))
        self.assertEqual(self.api.checkpoints, [])

    async def test_reasoning_only_and_tool_call_only_messages_count_as_content(self):
        writer = self.writer()
        self.assertTrue(await writer.write({"role": "assistant", "content": "", "reasoning_content": "thinking"}))
        self.assertTrue(
            await writer.write(
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "f", "arguments": "{}"}}]}
            )
        )

    async def test_disabled_without_a_thread_id(self):
        writer = StreamCheckpointWriter(self.api, None, interval=0.0)
        self.assertFalse(writer.enabled)
        self.assertFalse(await writer.write({"role": "assistant", "content": "x"}))
        self.assertEqual(self.api.checkpoints, [])


class TestGuardMessages(unittest.TestCase):
    """`thread.messages` may only grow unless the caller opts into truncation."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.ctx = MockCtx()
        cls.db = AppDB(cls.ctx, os.path.join(cls.tmp, "app.sqlite"))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.ctx.errors = []
        self.thread_id = asyncio.run(self.db.create_thread_async({"title": "t", "messages": history_messages()}))

    def stored(self):
        row = self.db.get_thread(self.thread_id)
        return json.loads(row["messages"])

    def test_appending_is_allowed(self):
        grown = self.stored() + [{"role": "assistant", "content": "answer"}]
        asyncio.run(self.db.update_thread_async(self.thread_id, {"messages": grown}))
        self.assertEqual(len(self.stored()), 6)

    def test_shrinking_write_cannot_erase_history(self):
        """A single-turn request must not replace a whole conversation."""
        asyncio.run(
            self.db.update_thread_async(
                self.thread_id,
                {"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]},
            )
        )
        stored = self.stored()
        self.assertEqual([m["content"] for m in stored[:5]], [m["content"] for m in history_messages()])
        self.assertTrue(self.ctx.errors, "the refused shrink should be logged")

    def test_shrinking_write_still_keeps_its_new_messages(self):
        """History is preserved and genuinely new messages are appended, not dropped."""
        asyncio.run(self.db.update_thread_async(self.thread_id, {"messages": [{"role": "user", "content": "new ask"}]}))
        stored = self.stored()
        self.assertEqual(len(stored), 6)
        self.assertEqual(stored[-1]["content"], "new ask")

    def test_truncate_opts_in_to_rewriting_history(self):
        """Edit/redo/delete/compact legitimately shrink history."""
        kept = history_messages()[:3]
        asyncio.run(self.db.update_thread_async(self.thread_id, {"messages": kept, "truncate": True}))
        self.assertEqual(len(self.stored()), 3)

    def test_truncate_is_not_persisted_as_a_column(self):
        asyncio.run(self.db.update_thread_async(self.thread_id, {"messages": history_messages(), "truncate": True}))
        self.assertNotIn("truncate", self.db.get_thread(self.thread_id))

    def test_updates_that_dont_touch_messages_are_unaffected(self):
        asyncio.run(self.db.update_thread_async(self.thread_id, {"title": "renamed"}))
        self.assertEqual(self.db.get_thread(self.thread_id)["title"], "renamed")
        self.assertEqual(len(self.stored()), 5)

    def test_echoed_in_flight_message_is_never_persisted(self):
        """A client round-tripping the merged DTO must not commit a partial as history."""
        echoed = history_messages() + [{"role": "assistant", "content": "half a reply", "streaming": True}]
        asyncio.run(self.db.update_thread_async(self.thread_id, {"messages": echoed}))
        stored = self.stored()
        self.assertEqual(len(stored), 5)
        self.assertFalse(any(m.get("streaming") for m in stored))

    def test_echoed_in_flight_message_cannot_mask_a_shrink(self):
        """Filtering the partial happens before the guard, so it can't buy a shrink."""
        echoed = history_messages()[:3] + [{"role": "assistant", "content": "half", "streaming": True}]
        asyncio.run(self.db.update_thread_async(self.thread_id, {"messages": echoed}))
        self.assertEqual(len(self.stored()), 5)

    def test_streaming_checkpoint_leaves_messages_alone(self):
        asyncio.run(self.db.update_thread_async(self.thread_id, {"streamingMessage": {"role": "assistant", "content": "partial"}}))
        self.assertEqual(len(self.stored()), 5)
        self.assertEqual(json.loads(self.db.get_thread(self.thread_id)["streamingMessage"])["content"], "partial")


class TestProviderBaseHelpers(unittest.TestCase):
    """Providers get these off the OpenAiCompatible base rather than importing them."""

    def setUp(self):
        self.provider = OpenAiCompatible(id="test", api="https://example.com")

    def test_stream_writer_reads_thread_and_user_from_context(self):
        writer = self.provider.stream_writer({"threadId": "t1", "user": "alice"})
        self.assertIsInstance(writer, StreamCheckpointWriter)
        self.assertEqual(writer.thread_id, "t1")
        self.assertEqual(writer.user, "alice")

    def test_stream_writer_without_context_is_disabled(self):
        self.assertFalse(self.provider.stream_writer().enabled)

    def test_stream_error_message(self):
        self.assertEqual(self.provider.stream_error_message({"code": 429, "message": "rate limited"}), "rate limited")
        self.assertEqual(self.provider.stream_error_message("boom"), "boom")
        self.assertEqual(self.provider.stream_error_message(None, "fallback"), "fallback")


class TestStreamingClientTimeout(unittest.TestCase):
    def test_streaming_has_no_total_timeout(self):
        """A long response must not be killed mid-stream by the total timeout."""
        streaming = get_client_timeout(streaming=True)
        self.assertIsNone(streaming.total)
        self.assertIsNotNone(streaming.sock_read)

    def test_non_streaming_keeps_total_timeout(self):
        self.assertIsNotNone(get_client_timeout().total)


class TestStreamDiesMidResponse(unittest.IsolatedAsyncioTestCase):
    """End-to-end through the OpenRouter provider with a stream that dies."""

    @classmethod
    def setUpClass(cls):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cls.temp_dir = tempfile.TemporaryDirectory()
        temp_config = os.path.join(cls.temp_dir.name, "llms.json")
        temp_providers = os.path.join(cls.temp_dir.name, "providers.json")

        with open(os.path.join(root, "llms", "llms.json")) as f:
            config = json.load(f)
        with open(os.path.join(root, "llms", "providers.json")) as f:
            providers = json.load(f)
        with open(temp_config, "w") as f:
            json.dump(config, f)
        with open(temp_providers, "w") as f:
            json.dump(providers, f)

        cli(f"ls openrouter --config {temp_config} --providers {temp_providers}")
        cls.app = get_app()
        cls.provider = cls.app.get_providers().get("openrouter") or g_handlers.get("openrouter")

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown()
        cls.temp_dir.cleanup()

    def make_session(self, lines, die_after, exc):
        response = AsyncMock()
        response.status = 200
        response.content = DyingStreamReader(lines, die_after, exc)

        post = MagicMock()
        post.__aenter__.return_value = response
        post.__aexit__.return_value = None

        session = MagicMock()
        session.post.return_value = post
        session.__aenter__.return_value = session
        session.__aexit__.return_value = None
        return session

    async def run_stream(self, api, lines, die_after, exc, expect_raises=True):
        chat = {
            "model": "openai/gpt-4o",
            "messages": [dict(m) for m in history_messages()],
            "stream": True,
        }
        context = {"threadId": "t1", "user": None, "chat": chat}

        previous = self.app.threads
        self.app.threads = api
        try:
            with patch("aiohttp.ClientSession", return_value=self.make_session(lines, die_after, exc)):
                if expect_raises:
                    with self.assertRaises(Exception) as cm:
                        await self.provider.chat(chat, context=context)
                    return cm.exception
                return await self.provider.chat(chat, context=context)
        finally:
            self.app.threads = previous

    async def test_dying_stream_leaves_history_untouched(self):
        api = FakeThreadsApi({"id": "t1", "messages": history_messages()})
        lines = [
            'data: {"id":"g1","model":"m","choices":[{"index":0,"delta":{"content":"chunk-A "}}]}\n',
            'data: {"id":"g1","model":"m","choices":[{"index":0,"delta":{"content":"chunk-B "}}]}\n',
            "data: [DONE]\n",
        ]

        await self.run_stream(api, lines, die_after=2, exc=asyncio.TimeoutError("connection died"))

        self.assertEqual(api.message_writes, [], "a dying stream must not write messages")
        self.assertEqual(api.thread["messages"], history_messages())
        # the partial that did arrive is flushed to the streaming checkpoint
        self.assertEqual(api.thread["streamingMessage"]["content"], "chunk-A chunk-B ")

    async def test_stream_dying_before_any_content_writes_nothing(self):
        api = FakeThreadsApi({"id": "t1", "messages": history_messages()})
        lines = ['data: {"id":"g1","model":"m","choices":[{"index":0,"delta":{"role":"assistant"}}]}\n']

        await self.run_stream(api, lines, die_after=1, exc=asyncio.TimeoutError("connection died"))

        self.assertEqual(api.message_writes, [])
        self.assertEqual(api.checkpoints, [])
        self.assertEqual(api.thread["messages"], history_messages())

    async def test_repeated_retries_never_shrink_the_thread(self):
        api = FakeThreadsApi({"id": "t1", "messages": history_messages()})
        lines = ['data: {"id":"g1","model":"m","choices":[{"index":0,"delta":{"content":"retry text"}}]}\n']

        for _ in range(3):
            await self.run_stream(api, lines, die_after=1, exc=asyncio.TimeoutError("connection died"))
            self.assertEqual(api.thread["messages"], history_messages())
        self.assertEqual(api.message_writes, [])

    async def test_error_chunk_mid_stream_is_surfaced_and_keeps_history(self):
        """An upstream failure reported as an SSE chunk must not look like a clean stop."""
        api = FakeThreadsApi({"id": "t1", "messages": history_messages()})
        lines = [
            'data: {"id":"g1","model":"m","choices":[{"index":0,"delta":{"content":"partial answer"}}]}\n',
            'data: {"error":{"code":429,"message":"moonshotai/kimi-k3 is temporarily rate-limited upstream"}}\n',
            "data: [DONE]\n",
        ]

        ex = await self.run_stream(api, lines, die_after=len(lines), exc=None)

        self.assertIn("rate-limited upstream", str(ex))
        self.assertEqual(api.message_writes, [])
        self.assertEqual(api.thread["messages"], history_messages())
        self.assertEqual(api.thread["streamingMessage"]["content"], "partial answer")

    async def test_completed_stream_checkpoints_but_still_never_writes_messages(self):
        """Committing the finished message is chat_response's job, not the stream's."""
        api = FakeThreadsApi({"id": "t1", "messages": history_messages()})
        lines = [
            'data: {"id":"g1","model":"m","choices":[{"index":0,"delta":{"content":"all done"}}]}\n',
            'data: {"id":"g1","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2}}\n',
            "data: [DONE]\n",
        ]

        res = await self.run_stream(api, lines, die_after=len(lines), exc=None, expect_raises=False)

        self.assertEqual(res["choices"][0]["message"]["content"], "all done")
        self.assertEqual(api.message_writes, [])
        self.assertEqual(api.thread["streamingMessage"]["content"], "all done")


if __name__ == "__main__":
    unittest.main()
