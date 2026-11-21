import copy
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

from llms.main import AppExtensions, ExtensionContext
from llms.provider_replay import GeminiReplayFilters, ProviderReplayContext
from tests import test_google_tool_combination as google


class ProviderReplayFilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.harness = google.GoogleToolCombinationTests()
        self.harness.setUp()
        self.extension = ExtensionContext.__new__(ExtensionContext)
        self.extension.app = SimpleNamespace(provider_replay_filters=[])
        self.extension.verbose = False
        self.harness.ctx.filter_provider_replay = self.extension.filter_provider_replay

    def register(self, handler=GeminiReplayFilters.exclude_completed_turn_tool_invocations):
        self.extension.register_provider_replay_filter(handler)

    async def signed(self, parts=None):
        message, _ = await self.harness.request(self.harness.chat(), parts if parts is not None else google.PARTS)
        return message

    async def payload(self, messages, stream=False, **extra):
        before = copy.deepcopy(messages)
        _, payload = await self.harness.request(self.harness.chat(messages=messages, stream=stream, **extra))
        self.assertEqual(messages, before)
        return payload

    async def test_default_replays_all_parts_in_completed_turns(self):
        first = await self.signed()
        payload = await self.payload([first, {"role": "user", "content": "Follow-up"}])
        self.assertEqual(payload["contents"][0]["parts"], google.PARTS)

    async def test_opt_in_preserves_functions_text_unknown_parts_and_signatures(self):
        parts = google.PARTS + [{"thoughtSignature": "standalone"}, {"futureField": {"value": 1}}]
        first = await self.signed(parts)
        self.register()
        for stream in (False, True):
            payload = await self.payload(
                [
                    {"role": "user", "content": "First"},
                    first,
                    {"role": "tool", "tool_call_id": "fetch-1", "content": "ok"},
                    {"role": "user", "content": "Follow-up"},
                ],
                stream,
            )
            self.assertEqual(
                payload["contents"][1]["parts"], [p for p in parts if "toolCall" not in p and "toolResponse" not in p]
            )
            self.assertEqual(payload["contents"][2]["parts"][0]["functionResponse"]["id"], "fetch-1")
            self.assertTrue(payload["toolConfig"]["includeServerSideToolInvocations"])

    async def test_current_loop_and_parallel_function_results_are_untouched(self):
        first = await self.signed()
        self.register()
        for prefix in ([], [{"role": "user", "content": "Current user prompt"}]):
            payload = await self.payload(prefix + [first, {"role": "tool", "tool_call_id": "fetch-1", "content": "ok"}])
            self.assertEqual(payload["contents"][len(prefix)]["parts"], google.PARTS)

    async def test_boundary_distinguishes_previous_and_current_assistants(self):
        first = await self.signed()
        current = copy.deepcopy(first)
        self.register()
        payload = await self.payload(
            [
                first,
                {"role": "user", "content": [{"type": "text", "text": "Next"}]},
                current,
                {"role": "tool", "tool_call_id": "fetch-1", "content": "ok"},
            ]
        )
        self.assertEqual(len(payload["contents"][0]["parts"]), 3)
        self.assertEqual(payload["contents"][2]["parts"], google.PARTS)

    async def test_filters_run_in_order_with_resolved_identity_and_detached_content(self):
        first = await self.signed()
        seen = []

        def one(replay):
            seen.append(
                (
                    replay.provider.id,
                    replay.model,
                    replay.message_index,
                    replay.last_user_message_index,
                    replay.is_current_turn,
                )
            )
            replay.content["parts"].append({"text": "extension"})

        def two(replay):
            self.assertEqual(replay.content["parts"][-1], {"text": "extension"})
            replay.content["parts"][-1]["text"] += " two"

        self.register(one)
        self.register(two)
        for _ in range(2):
            payload = await self.payload([first, {"role": "user", "content": "Next"}])
            self.assertEqual(payload["contents"][0]["parts"][-1], {"text": "extension two"})
        self.assertEqual(seen, [("google", google.MODEL, 0, 1, False)] * 2)
        self.assertEqual(first["provider_metadata"]["google"]["content"]["parts"], google.PARTS)

    async def test_mismatched_model_or_provider_does_not_call_filter(self):
        first = await self.signed()
        calls = []
        self.register(calls.append)
        for key, value in (("model", "different"), ("provider", "other")):
            message = copy.deepcopy(first)
            message["provider_metadata"]["google"][key] = value
            await self.payload([message, {"role": "user", "content": "Next"}])
        self.assertEqual(calls, [])

    async def test_empty_historical_content_is_omitted_without_empty_gemini_message(self):
        first = await self.signed([google.PARTS[1], google.PARTS[-1]])
        self.register()
        payload = await self.payload([first, {"role": "user", "content": "Next"}])
        self.assertEqual(payload["contents"], [{"role": "user", "parts": [{"text": "Next"}]}])

    async def test_filter_failure_stops_before_network_request(self):
        first = await self.signed()

        def broken(replay):
            replay.content["parts"].clear()
            raise RuntimeError("extension failed")

        self.register(broken)
        with self.assertRaisesRegex(RuntimeError, "extension failed"):
            await self.payload([first])
        self.assertEqual(first["provider_metadata"]["google"]["content"]["parts"], google.PARTS)

    async def test_filter_cannot_bypass_disabled_tool_combination_guard(self):
        first = await self.signed()
        self.register()
        with self.assertRaisesRegex(ValueError, "requires Gemini tool context circulation"):
            await self.payload([first, {"role": "user", "content": "Next"}], tool_combination=False)

    async def test_opt_in_runs_through_actual_function_executor_loop(self):
        self.register()
        await self.harness.test_actual_function_loop_executes_only_custom_calls()

    async def test_invalid_parts_abort_request_preparation(self):
        first = await self.signed()
        self.register(lambda replay: replay.content.update(parts="invalid"))
        with self.assertRaisesRegex(ValueError, "content.parts must be a list"):
            await self.payload([first])

    async def test_parallel_function_responses_preserve_ids_under_filtering(self):
        parts = copy.deepcopy(google.PARTS)
        parallel = copy.deepcopy(parts[3])
        parallel["functionCall"]["id"] = "fetch-2"
        parts.append(parallel)
        first = await self.signed(parts)
        self.register()
        history = [first] + [
            {"role": "tool", "tool_call_id": call_id, "content": "ok"} for call_id in ("fetch-1", "fetch-2")
        ]
        history.append({"role": "user", "content": "Follow-up"})
        payload = await self.payload(history)
        self.assertEqual(
            [part["functionResponse"]["id"] for part in payload["contents"][1]["parts"]], ["fetch-1", "fetch-2"]
        )
        self.assertEqual(
            [part["functionCall"]["id"] for part in payload["contents"][0]["parts"] if "functionCall" in part],
            ["fetch-1", "fetch-2"],
        )

    def test_helper_ignores_other_provider_types(self):
        content = {"parts": copy.deepcopy(google.PARTS)}
        replay = ProviderReplayContext(SimpleNamespace(sdk="@ai-sdk/other"), "model", 0, 1, content)
        GeminiReplayFilters.exclude_completed_turn_tool_invocations(replay)
        self.assertEqual(content["parts"], google.PARTS)

    def test_app_registry_is_instance_local(self):
        module = __import__("llms.main", fromlist=["g_app"])
        previous = module.g_app
        try:
            first = AppExtensions(SimpleNamespace(), {})
            second = AppExtensions(SimpleNamespace(), {})
            first.provider_replay_filters.append(lambda _: None)
            self.assertEqual(second.provider_replay_filters, [])
        finally:
            module.g_app = previous

    def test_example_extension_registers_opt_in_without_auto_enabling_it(self):
        path = Path(__file__).resolve().parents[1] / "examples/gemini_replay_extension.py"
        spec = importlib.util.spec_from_file_location("gemini_replay_example", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(self.extension.app.provider_replay_filters, [])
        module.__install__(self.extension)
        self.assertEqual(
            self.extension.app.provider_replay_filters, [GeminiReplayFilters.exclude_completed_turn_tool_invocations]
        )
