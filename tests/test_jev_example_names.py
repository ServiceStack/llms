import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from llms.extensions.jev import bundled_recipes, install
from llms.extensions.jev.client import normalize_answers
from llms.extensions.jev.example_names import ExampleNamer
from llms.extensions.jev.schema import ValidationError, compile_request
from llms.extensions.jev.storage import JevStore
from tests.test_jev import sample_response, starter


class ExampleNameTests(IsolatedAsyncioTestCase):
    def setUp(self):
        recipe = starter()
        self.run = {
            "status": "succeeded",
            "recipe": recipe,
            "input": recipe["examples"][0]["input"],
            "answers": normalize_answers(sample_response(recipe), recipe["questions"]),
            "response": {"apiKey": "private-secret"},
            "id": "private-id",
            "usage": {"cost": 1},
        }
        self.template = {
            "model": "summary-model",
            "temperature": 0.1,
            "max_tokens": 80,
            "messages": [{"role": "system", "content": "Chat title instructions"}],
            "tools": ["private-tool"],
            "tool_choice": "auto",
            "metadata": {"projectId": "private-project"},
            "threadId": "private-thread",
            "stream": True,
        }
        self.provider = SimpleNamespace(
            provider_model=lambda model: model == "summary-model",
            model_info=lambda _: {"limit": {"context": 4096}, "modalities": {"output": ["text"]}},
            chat=AsyncMock(
                return_value={"choices": [{"message": {"content": 'Name: "Late delivery needs attention"'}}]}
            ),
        )
        self.ctx = SimpleNamespace(
            config={"defaults": {"summarize": copy.deepcopy(self.template)}},
            get_providers=lambda: {"summary": self.provider},
        )
        self.namer = ExampleNamer(self.ctx)

    async def test_uses_summarize_model_with_actual_input_and_results_and_no_history(self):
        original = copy.deepcopy(self.run)
        result = await self.namer.suggest(self.run, "alice")
        self.assertEqual(result, {"label": "Late delivery needs attention", "model": "summary-model"})
        chat = self.provider.chat.call_args.args[0]
        self.assertEqual(chat["model"], "summary-model")
        self.assertEqual(chat["max_tokens"], 80)
        self.assertFalse(chat["stream"])
        source = json.loads(chat["messages"][1]["content"])
        self.assertEqual(source["input"], self.run["input"])
        self.assertEqual(source["answers"], self.run["answers"])
        for key in ("tools", "tool_choice", "metadata", "threadId"):
            self.assertNotIn(key, chat)
        for secret in ("private-secret", "private-id", "private-project", "private-thread"):
            self.assertNotIn(secret, json.dumps(chat))
        context = self.provider.chat.call_args.kwargs["context"]
        self.assertEqual(context["user"], "alice")
        self.assertEqual(context["purpose"], "jev_example_name")
        self.assertEqual(context["tools"], "none")
        self.assertTrue(context["nohistory"] and context["nostore"])
        self.assertEqual(self.run, original)
        self.assertEqual(self.ctx.config["defaults"]["summarize"], self.template)

    async def test_disabled_unavailable_and_nontext_models_never_dispatch(self):
        self.ctx.config["defaults"]["summarize"] = None
        with self.assertRaises(web.HTTPServiceUnavailable):
            await self.namer.suggest(self.run, "alice")
        self.ctx.config["defaults"]["summarize"] = {"model": "missing"}
        with self.assertRaises(web.HTTPServiceUnavailable):
            await self.namer.suggest(self.run, "alice")
        self.ctx.config["defaults"]["summarize"] = self.template
        self.provider.model_info = lambda _: {"modalities": {"output": ["image"]}}
        with self.assertRaises(web.HTTPServiceUnavailable):
            await self.namer.suggest(self.run, "alice")
        self.provider.chat.assert_not_called()

    async def test_unsuccessful_runs_are_rejected_before_calling_model(self):
        for status in ("pending", "running", "failed", "cancelled", "interrupted"):
            with self.assertRaises(ValidationError):
                await self.namer.suggest({**self.run, "status": status}, "alice")
        self.provider.chat.assert_not_called()

    async def test_failures_and_invalid_model_results_are_actionable_without_retry(self):
        for failure, error in ((TimeoutError(), web.HTTPGatewayTimeout), (RuntimeError("secret"), web.HTTPBadGateway)):
            self.provider.chat.reset_mock()
            self.provider.chat.side_effect = failure
            with self.assertRaises(error) as result:
                await self.namer.suggest(self.run, "alice")
            self.assertIn("Enter a name yourself", result.exception.text)
            self.assertNotIn("secret", result.exception.text)
            self.provider.chat.assert_awaited_once()
        self.provider.chat.side_effect = None
        for output in (None, {}, {"choices": []}, {"choices": [{"message": {"content": "   "}}]}):
            self.provider.chat.return_value = output
            with self.assertRaises(web.HTTPBadGateway):
                await self.namer.suggest(self.run, "alice")

    async def test_prompt_and_name_lengths_are_bounded(self):
        self.run["input"] = {"ticket": "x" * 30000}
        self.provider.chat.return_value = {"choices": [{"message": {"content": "x" * 200}}]}
        name = await self.namer.suggest(self.run, "alice")
        self.assertEqual(len(name["label"]), 120)
        self.assertLessEqual(len(self.provider.chat.call_args.args[0]["messages"][1]["content"]), 3584)

    async def test_endpoint_only_reads_the_authenticated_users_completed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            app, cleanups = web.Application(), []
            self.ctx.assert_username = lambda request: request.headers.get("X-User", "alice")
            self.ctx.get_user_path = lambda user: str(Path(directory) / user)
            self.ctx.get_registered_provider = lambda _: None
            self.ctx.register_cleanup_handler = cleanups.append
            for method in ("get", "post", "put", "delete"):
                setattr(
                    self.ctx,
                    "add_" + method,
                    lambda path, handler, method=method: app.router.add_route(
                        method.upper(), "/ext/jev/" + path, handler
                    ),
                )
            install(self.ctx)
            db = JevStore(self.ctx.get_user_path("alice"), bundled_recipes())
            recipe, inputs = self.run["recipe"], self.run["input"]
            run, _ = db.submit("name-test", recipe, inputs, compile_request(recipe, inputs), "owner")
            url = "/ext/jev/runs/" + run["id"] + "/example-name"
            try:
                async with TestClient(TestServer(app)) as client:
                    self.assertEqual((await client.post(url)).status, 400)
                    db.finish(run["id"], "succeeded", sample_response(recipe), self.run["answers"])
                    before = db.run(run["id"])
                    self.assertEqual((await client.post(url, headers={"X-User": "bob"})).status, 404)
                    response = await client.post(url)
                    self.assertEqual(response.status, 200)
                    self.assertEqual((await response.json())["label"], "Late delivery needs attention")
                    self.assertEqual(db.run(run["id"]), before)
                    self.provider.chat.assert_awaited_once()
            finally:
                for cleanup in cleanups:
                    await cleanup()
