"""Sarvam AI registration, credentials, model resolution and catalog refresh."""

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


class TestSarvam(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "llms"
        config = json.loads((root / "llms.json").read_text())
        self.config = {
            "defaults": config["defaults"],
            "providers": {"sarvam": config["providers"]["sarvam"]},
        }
        self.providers = json.loads((root / "providers.json").read_text())
        self.providers = {"sarvam": self.providers["sarvam"]}
        self.patches = [
            patch.object(main, "g_config", {}),
            patch.object(main, "g_providers", {}),
            patch.object(main, "g_handlers", {}),
            patch.object(main, "g_app", SimpleNamespace(all_providers=[main.OpenAiCompatible])),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_catalog_entry_uses_token_factory_v2(self):
        sarvam = self.providers["sarvam"]
        self.assertEqual(sarvam["api"], "https://api.sarvam.ai/v2")
        self.assertEqual(sarvam["env"], ["SARVAM_API_KEY"])
        self.assertEqual(sarvam["npm"], "@ai-sdk/openai-compatible")
        self.assertTrue(
            {"sarvam-105b", "glm5.3", "gemma4", "deepseekv4.1-flash-1017"}.issubset(sarvam["models"]),
        )

    def test_sarvam_105b_uses_list_price_and_limits(self):
        # Sarvam publishes INR per 1M tokens; USD values use the pricing page's rate of 94 INR per USD.
        # sarvam-105b list price: 15 INR input, 5 INR cached input, 60 INR output.
        models = self.providers["sarvam"]["models"]
        for model_id in ("sarvam-105b", "sarvam-105b-conversations"):
            cost = models[model_id]["cost"]
            self.assertAlmostEqual(cost["input"], 15 / 94, places=6)
            self.assertAlmostEqual(cost["cache_read"], 5 / 94, places=6)
            self.assertAlmostEqual(cost["output"], 60 / 94, places=6)
        self.assertEqual(models["sarvam-105b"]["limit"]["context"], 128000)
        self.assertEqual(models["sarvam-105b-conversations"]["limit"], {"context": 32000, "output": 8192})

    def test_environment_key_enables_provider_and_resolves_model(self):
        with patch.dict(os.environ, {"SARVAM_API_KEY": "test-sarvam-key"}, clear=True):
            handlers = main.init_llms(self.config, self.providers)
        provider = handlers["sarvam"]
        self.assertIsInstance(provider, main.OpenAiCompatible)
        self.assertEqual(provider.chat_url, "https://api.sarvam.ai/v2/chat/completions")
        self.assertEqual(provider.headers["Authorization"], "Bearer test-sarvam-key")
        self.assertEqual(provider.provider_model("sarvam-105b"), "sarvam-105b")
        self.assertEqual(provider.provider_model("sarvam/glm5.3"), "glm5.3")

    def test_missing_key_leaves_provider_unavailable(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("sarvam", main.init_llms(self.config, self.providers))

    def test_model_list_keeps_same_name_from_each_provider(self):
        sarvam_models = self.providers["sarvam"]["models"]
        glm = sarvam_models["glm5.3"]
        original = copy.deepcopy(glm)
        handlers = {
            "zai": SimpleNamespace(models={glm["id"]: copy.deepcopy(glm)}),
            "sarvam": SimpleNamespace(models=sarvam_models),
        }
        with patch.object(main, "g_handlers", handlers):
            models = main.get_active_models()
        matches = [model for model in models if model["id"] == glm["id"]]
        self.assertEqual({model["provider"] for model in matches}, {"zai", "sarvam"})
        self.assertEqual(glm, original)

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
