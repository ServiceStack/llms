"""Gemini tool context must survive conversion, streaming, storage, and replay."""

import copy
import importlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from llms.db import count_tokens_approx
from llms.extensions.app.db import AppDB
from llms.extensions.providers.google import install_google
from llms.main import OpenAiCompatible, StreamCheckpointWriter
from tests.test_app_db_provider import MockContext

main = importlib.import_module("llms.main")
MODEL = "gemini-3.8-flash"
TOOLS = [
    {"type": "file_search", "file_search": {"file_search_store_names": ["fileSearchStores/docs"]}},
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a URL",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
]
PARTS = [
    {"text": "Consulting documents", "thought": True, "thoughtSignature": "reasoning-signature"},
    {"toolCall": {"toolType": "FILE_SEARCH", "id": "search-1"}, "thoughtSignature": "search-signature"},
    {"text": "Checking a URL next.", "thoughtSignature": "text-signature"},
    {
        "functionCall": {"name": "fetch_url", "args": {"url": "https://example.com"}, "id": "fetch-1"},
        "thoughtSignature": "function-signature",
    },
    {"toolResponse": {"toolType": "FILE_SEARCH", "id": "search-1"}, "thoughtSignature": "result-signature"},
]


class Stream:
    def __init__(self, chunks, fail=False):
        self.chunks = chunks
        self.fail = fail

    async def __aiter__(self):
        for chunk in self.chunks:
            yield ("data: " + json.dumps(chunk) + "\n").encode()
        if self.fail:
            raise ConnectionError("stream interrupted")


def candidate(parts, **extra):
    return {"candidates": [{"index": 0, "content": {"role": "model", "parts": parts}, **extra}]}


class GoogleToolCombinationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        providers = []
        self.ctx = SimpleNamespace(
            add_provider=providers.append,
            log=lambda *_: None,
            dbg=lambda *_: None,
            debug=False,
            MOCK=False,
            get_client_timeout=lambda **_: None,
            log_json=lambda value: value,
            should_cancel_thread=lambda _: False,
        )
        install_google(self.ctx)
        self.provider = providers[0](id="google", api_key="test-key")

    def chat(self, **extra):
        return {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Search docs, then fetch the URL"}],
            "tools": copy.deepcopy(TOOLS),
            **extra,
        }

    def mock_session(self, chunks, fail=False):
        response = AsyncMock(status=200)
        response.content = Stream(chunks, fail=fail)
        post = MagicMock()
        post.__aenter__.return_value = response
        session = MagicMock()
        session.post.return_value = post
        session.__aenter__.return_value = session
        return session

    async def request(self, chat, parts=None, grounding=None):
        result = candidate(parts if parts is not None else [{"text": "Done"}], finishReason="STOP")
        if grounding:
            result["candidates"][0]["groundingMetadata"] = grounding
        session = self.mock_session([result])
        before = copy.deepcopy(chat)
        with (
            patch("aiohttp.ClientSession", return_value=session),
            patch.object(self.provider, "response_json", AsyncMock(return_value=result)),
        ):
            response = await self.provider.chat(chat)
        self.assertEqual(chat, before, "provider conversion must not mutate authoritative history")
        return response["choices"][0]["message"], json.loads(session.post.call_args.kwargs["data"])

    async def test_supported_models_and_aliases_send_both_tools(self):
        for model in (
            MODEL,
            "gemini-3.5-flash-lite",
            "gemini-3.1-pro-preview",
            "gemini-4-argon",
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
        ):
            for stream in (False, True):
                with self.subTest(model=model, stream=stream):
                    _, payload = await self.request(self.chat(model=model, stream=stream))
                    self.assertIn("file_search", payload["tools"][0])
                    self.assertEqual(payload["tools"][0]["function_declarations"][0]["name"], "fetch_url")
                    self.assertEqual(
                        payload["toolConfig"],
                        {
                            "includeServerSideToolInvocations": True,
                            "functionCallingConfig": {"mode": "VALIDATED"},
                        },
                    )

    async def test_older_models_and_explicit_capability_overrides(self):
        for model, extra, enabled in (
            ("gemini-2.5-flash", {}, False),
            (MODEL, {"tool_combination": False}, False),
            ("custom-alias", {"tool_combination": True}, True),
        ):
            with self.subTest(model=model, extra=extra):
                _, payload = await self.request(self.chat(model=model, **extra))
                self.assertEqual("function_declarations" in payload["tools"][0], enabled)
                self.assertEqual("toolConfig" in payload, enabled)
        self.provider.tool_combination = True
        _, payload = await self.request(self.chat(model="custom-alias"))
        self.assertTrue(payload["toolConfig"]["includeServerSideToolInvocations"])
        with patch.object(self.provider, "model_info", return_value={"tool_combination": False}):
            _, payload = await self.request(self.chat())
        self.assertNotIn("toolConfig", payload)

    async def test_single_tool_modes_keep_existing_payloads(self):
        for tools in ([TOOLS[0]], [TOOLS[1]], []):
            with self.subTest(tools=tools):
                _, payload = await self.request(self.chat(tools=tools))
                self.assertNotIn("toolConfig", payload)

    async def test_combined_tool_choice_never_uses_unsupported_auto_mode(self):
        for choice, expected in (
            ("auto", {"mode": "VALIDATED"}),
            ("none", {"mode": "NONE"}),
            ("required", {"mode": "ANY"}),
            (
                {"type": "function", "function": {"name": "fetch_url"}},
                {"mode": "ANY", "allowedFunctionNames": ["fetch_url"]},
            ),
        ):
            with self.subTest(choice=choice):
                _, payload = await self.request(self.chat(tool_choice=choice))
                self.assertEqual(payload["toolConfig"]["functionCallingConfig"], expected)

    async def test_legacy_call_without_provider_id_matches_its_synthesized_id(self):
        original = {"functionCall": {"name": "fetch_url", "args": {"url": "https://example.com"}}}
        first, _ = await self.request(self.chat(model="gemini-2.5-flash", tools=[TOOLS[1]]), [original])
        call_id = first["tool_calls"][0]["id"]
        _, payload = await self.request(
            self.chat(
                model="gemini-2.5-flash",
                tools=[TOOLS[1]],
                messages=[
                    first,
                    {"role": "tool", "tool_call_id": call_id, "content": "ok"},
                ],
            )
        )
        self.assertEqual(payload["contents"][0]["parts"][0]["functionCall"]["id"], call_id)
        self.assertEqual(payload["contents"][1]["parts"][0]["functionResponse"]["id"], call_id)
        self.assertEqual(first["provider_metadata"]["google"]["content"]["parts"], [original])

    async def test_complete_parts_replay_once_with_matching_function_response(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                first, _ = await self.request(self.chat(stream=stream), PARTS)
                self.assertEqual([tc["id"] for tc in first["tool_calls"]], ["fetch-1"])
                raw = first["provider_metadata"]["google"]["content"]
                self.assertEqual(raw, {"role": "model", "parts": PARTS})
                result = {"role": "tool", "tool_call_id": "fetch-1", "content": '{"body":"hello"}'}
                history = json.loads(json.dumps(self.chat()["messages"] + [first, result]))
                final_parts = [PARTS[1], PARTS[-1], {"text": "Answer", "thoughtSignature": "final-signature"}]
                final, payload = await self.request(self.chat(messages=history, stream=stream), final_parts)
                self.assertEqual(payload["contents"][1], raw)
                self.assertEqual(
                    payload["contents"][2],
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": "fetch_url",
                                    "id": "fetch-1",
                                    "response": {"body": "hello"},
                                }
                            }
                        ],
                    },
                )
                self.assertNotIn("tool_calls", final)
                history += [final, {"role": "user", "content": "Continue"}]
                _, payload = await self.request(self.chat(messages=history, tools=[TOOLS[0]], stream=stream))
                self.assertEqual(payload["contents"][3], {"role": "model", "parts": final_parts})
                self.assertTrue(payload["toolConfig"]["includeServerSideToolInvocations"])

    async def test_parallel_functions_return_one_user_turn(self):
        second_call = copy.deepcopy(PARTS[3])
        second_call["functionCall"]["id"] = "fetch-2"
        first, _ = await self.request(self.chat(), PARTS + [second_call])
        results = [{"role": "tool", "tool_call_id": call_id, "content": "ok"} for call_id in ("fetch-1", "fetch-2")]
        _, payload = await self.request(self.chat(messages=self.chat()["messages"] + [first] + results))
        self.assertEqual(
            [p["functionResponse"]["id"] for p in payload["contents"][-1]["parts"]], ["fetch-1", "fetch-2"]
        )
        self.assertEqual(payload["contents"][-1]["role"], "user")

    async def checkpoint_stream(self, parts, fail=False, other_candidate=False):
        chunks = [candidate([part]) for part in parts]
        if other_candidate:
            chunks[0]["candidates"].append(
                {
                    "index": 1,
                    "content": {
                        "parts": [
                            {"functionCall": {"name": "wrong_candidate", "args": {}}},
                        ]
                    },
                }
            )
        session = self.mock_session(chunks, fail=fail)
        checkpoints = []

        async def write(_, message, **kwargs):
            checkpoints.append(copy.deepcopy(message))

        writer = StreamCheckpointWriter(SimpleNamespace(checkpoint_stream_async=write), "thread", interval=60)
        with (
            patch("aiohttp.ClientSession", return_value=session),
            patch.object(self.provider, "stream_writer", return_value=writer),
        ):
            if fail:
                with self.assertRaises(ConnectionError):
                    await self.provider.chat(self.chat(), context={})
                return None, checkpoints
            response = await self.provider.chat(self.chat(), context={})
        return response["choices"][0]["message"], checkpoints

    async def test_stream_checkpoints_keep_order_and_signature_only_parts(self):
        parts = PARTS + [{"thoughtSignature": "standalone-signature"}]
        message, checkpoints = await self.checkpoint_stream(parts, other_candidate=True)
        self.assertEqual(message["provider_metadata"]["google"]["content"]["parts"], parts)
        self.assertEqual(checkpoints[-1]["provider_metadata"], message["provider_metadata"])
        self.assertEqual(checkpoints[0]["provider_metadata"]["google"]["content"]["parts"], parts[:1])
        self.assertEqual(len(message["tool_calls"]), 1)

    async def test_builtin_only_stream_is_checkpointed_and_not_executed(self):
        parts = [PARTS[1], PARTS[-1]]
        message, checkpoints = await self.checkpoint_stream(parts)
        self.assertNotIn("tool_calls", message)
        self.assertEqual(checkpoints[-1]["provider_metadata"]["google"]["content"]["parts"], parts)

    async def test_failed_stream_flushes_complete_received_replay_state(self):
        _, checkpoints = await self.checkpoint_stream(PARTS, fail=True)
        self.assertEqual(checkpoints[-1]["provider_metadata"]["google"]["content"]["parts"], PARTS)

    async def test_metadata_is_not_replayed_to_another_model_or_provider(self):
        first, _ = await self.request(self.chat(), PARTS)
        for model, origin in (("gemini-3.1-pro-preview", "google"), (MODEL, "other-google")):
            with self.subTest(model=model, origin=origin):
                message = copy.deepcopy(first)
                message["provider_metadata"]["google"]["provider"] = origin
                _, payload = await self.request(self.chat(model=model, messages=[message]))
                self.assertFalse(any("toolCall" in p for p in payload["contents"][0]["parts"]))
        generic = OpenAiCompatible(id="other", api="https://example.com")
        projected = await generic.process_chat({"messages": [copy.deepcopy(first)], "tool_combination": True})
        self.assertNotIn("provider_metadata", projected["messages"][0])
        self.assertNotIn("tool_combination", projected)
        self.assertIn("provider_metadata", first)

    async def test_disabling_circulation_on_existing_history_fails_explicitly(self):
        first, _ = await self.request(self.chat(), PARTS)
        with self.assertRaisesRegex(ValueError, "requires Gemini tool context circulation"):
            await self.provider.chat(self.chat(messages=[first], tool_combination=False))

    async def test_citations_remain_separate_from_opaque_tool_parts(self):
        grounding = {
            "groundingChunks": [{"retrievedContext": {"uri": "https://example.com/doc", "title": "Doc"}}],
            "groundingSupports": [{"segment": {"startIndex": 0, "endIndex": 6}, "groundingChunkIndices": [0]}],
        }
        for stream in (False, True):
            for gm in (None, grounding):
                with self.subTest(stream=stream, grounding=bool(gm)):
                    first, _ = await self.request(
                        self.chat(stream=stream), [PARTS[1], PARTS[-1], {"text": "Answer"}], gm
                    )
                    self.assertEqual("groundingMetadata" in first, bool(gm))
                    _, payload = await self.request(self.chat(messages=[first], stream=stream))
                    self.assertNotIn("groundingMetadata", payload["contents"][0])
                    if gm:
                        self.assertEqual(len(first["groundingMetadata"]["groundingSupports"]), 1)

    async def test_canonical_storage_and_restart_preserve_long_signatures(self):
        parts = copy.deepcopy(PARTS)
        parts[1]["thoughtSignature"] = "opaque" * 4000
        first, _ = await self.request(self.chat(), parts)
        first["timestamp"] = 2
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "app.sqlite")
            ctx = MockContext(path)
            db = AppDB(ctx, path)
            try:
                history = [
                    {"role": "user", "content": "Search", "timestamp": 1},
                    first,
                    {"role": "tool", "tool_call_id": "fetch-1", "content": "ok", "timestamp": 3},
                ]
                thread_id = db.create_thread({"messages": history}, "test_user")
            finally:
                db.close()
            db = AppDB(ctx, path)
            try:
                history = [row["message"] for row in db.get_chat_messages(thread_id)]
                self.assertEqual(history[1]["provider_metadata"], first["provider_metadata"])
                _, payload = await self.request(self.chat(messages=history))
                self.assertEqual(payload["contents"][1]["parts"], parts)
            finally:
                db.close()

    async def test_actual_function_loop_executes_only_custom_calls(self):
        responses = [candidate(PARTS), candidate([PARTS[1], PARTS[-1], {"text": "Finished"}])]
        for response in responses:
            response["usageMetadata"] = {"promptTokenCount": 50, "candidatesTokenCount": 10, "totalTokenCount": 60}
        session = self.mock_session([])
        execute = AsyncMock(return_value=("page contents", []))
        with (
            patch("aiohttp.ClientSession", return_value=session),
            patch.object(self.provider, "response_json", AsyncMock(side_effect=responses)),
            patch.object(main, "g_app", None),
            patch.object(main, "g_handlers", {"google": self.provider}),
            patch.object(main, "g_exec_tool", execute),
        ):
            response = await main.g_chat_completion(self.chat(stream=False), context={})
        execute.assert_awaited_once()
        self.assertEqual(execute.call_args.args[:2], ("fetch_url", {"url": "https://example.com"}))
        self.assertEqual(response["choices"][0]["message"]["content"], "Finished")
        second_payload = json.loads(session.post.call_args_list[1].kwargs["data"])
        self.assertEqual(second_payload["contents"][1]["parts"], PARTS)
        self.assertEqual(second_payload["contents"][2]["parts"][0]["functionResponse"]["id"], "fetch-1")

    def test_context_estimates_include_opaque_replay_state(self):
        message = {
            "role": "assistant",
            "content": "",
            "provider_metadata": self.provider.response_metadata(
                {"role": "model", "parts": [{"thoughtSignature": "opaque" * 4000}]}, self.chat(), True
            ),
        }
        self.assertGreater(count_tokens_approx([message]), 4000)

    def test_request_argument_accepts_explicit_false(self):
        for value, expected in (("false", False), ("true", True), (False, False), (True, True)):
            self.assertEqual(main.apply_args_to_chat({}, {"tool_combination": value}), {"tool_combination": expected})


if __name__ == "__main__":
    unittest.main()
