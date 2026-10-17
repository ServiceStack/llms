"""IteraCompute registration, credentials, model resolution and catalog refresh."""

import copy
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

main = importlib.import_module("llms.main")


class TestIteraCompute(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "llms"
        config = json.loads((root / "llms.json").read_text())
        self.config = {
            "defaults": config["defaults"],
            "providers": {"iteracompute": config["providers"]["iteracompute"]},
        }
        self.providers = json.loads((root / "providers.json").read_text())
        self.providers = {"iteracompute": self.providers["iteracompute"]}
        self.patches = [
            patch.object(main, "g_config", {}),
            patch.object(main, "g_providers", {}),
            patch.object(main, "g_handlers", {}),
            patch.object(main, "g_app", SimpleNamespace(all_providers=[main.OpenAiCompatible])),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_environment_key_enables_provider_and_resolves_model(self):
        with patch.dict(os.environ, {"ITERACOMPUTE_API_KEY": "test-itera-key"}, clear=True):
            handlers = main.init_llms(self.config, self.providers)
        provider = handlers["iteracompute"]
        self.assertIsInstance(provider, main.OpenAiCompatible)
        self.assertEqual(provider.chat_url, "https://api.iteracompute.com/v1/chat/completions")
        self.assertEqual(provider.headers["Authorization"], "Bearer test-itera-key")
        self.assertEqual(provider.provider_model("qwen3.8-27b"), "qwen/qwen3.8-27b")
        self.assertEqual(provider.provider_model("iteracompute/qwen/qwen3.8-27b"), "qwen/qwen3.8-27b")

    def test_missing_key_leaves_provider_unavailable(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("iteracompute", main.init_llms(self.config, self.providers))

    def test_model_list_keeps_same_name_from_each_provider(self):
        itera_models = self.providers["iteracompute"]["models"]
        qwen = itera_models["qwen/qwen3.8-27b"]
        original = copy.deepcopy(qwen)
        handlers = {
            "groq": SimpleNamespace(models={qwen["id"]: copy.deepcopy(qwen)}),
            "iteracompute": SimpleNamespace(models=itera_models),
        }
        with patch.object(main, "g_handlers", handlers):
            models = main.get_active_models()
        matches = [model for model in models if model["id"] == qwen["id"]]
        self.assertEqual({model["provider"] for model in matches}, {"groq", "iteracompute"})
        self.assertEqual(qwen, original)

    async def test_catalog_refresh_includes_configured_provider(self):
        catalog = copy.deepcopy(self.providers)
        catalog["unconfigured"] = {"id": "unconfigured", "models": {}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "providers.json"
            with (
                patch.object(main, "g_config", self.config),
                patch.object(main, "get_text", AsyncMock(return_value=json.dumps(catalog))) as get_text,
            ):
                await main.update_providers(str(path))
            get_text.assert_awaited_once_with("https://models.dev/api.json")
            self.assertEqual(json.loads(path.read_text()), self.providers)


if __name__ == "__main__":
    unittest.main()
