#!/usr/bin/env python3
"""
Unit tests for utility functions in llms.main module.
"""

import json
import os
import sys
import unittest

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.main import (
    check_provider_model,
    create_provider,
    create_provider_kwargs,
    load_config,
)

config_path = os.path.join(os.path.dirname(__file__), "..", "llms", "llms.json")
with open(config_path) as f:
    config = json.load(f)
providers_path = os.path.join(os.path.dirname(__file__), "..", "llms", "providers.json")
with open(providers_path) as f:
    providers = json.load(f)

check_models = {
    "groq": "openai/gpt-oss-20b",
    "codestral": "codestral-latest",
    "github-copilot": "gpt-5-mini",
    "github-models": "microsoft/phi-4",
    "zai-coding-plan": "glm-4.5-flash",
    "minimax": "MiniMax-M2",
    "openrouter_free": "mistralai/mistral-nemo:free",
    "ollama": "ministral-3-8b",
    "lmstudio": "mistralai/ministral-3-3b",
    "google": "gemini-flash-lite-latest",
    "anthropic": "claude-3-haiku-20240307",
    "openai": "gpt-4.1-nano",
    "xai": "grok-4-fast",
    "alibaba": "qwen-turbo",
    "zai": "glm-4.5-flash",
    "mistral": "ministral-3b-latest",
    "chutes": "openai/gpt-oss-20b",
    "deepseek": "deepseek-chat",
    "moonshotai": "kimi-k2-thinking",
    "nvidia": "nvidia/nvidia-nemotron-nano-9b-v2",
    "huggingface": "MiniMaxAI/MiniMax-M2",
    "fireworks-ai": "accounts/fireworks/models/gpt-oss-20b",
}


class TestCheckingProviders(unittest.IsolatedAsyncioTestCase):
    """Test Provider."""

    def setUp(self):
        load_config(config, providers, verbose=True)

    async def check_provider_model(self, provider_name, model):
        llms_provider = config["providers"][provider_name]
        if llms_provider is None:
            print(f"Skipping provider {provider_name} due to missing provider definition")
            return False
        if "enabled" in llms_provider and not llms_provider["enabled"]:
            print(f"Skipping disabled provider {provider_name}")
            return False
        provider_id = llms_provider.get("id", provider_name)
        provider_args = create_provider_kwargs(llms_provider, providers.get(provider_id))
        provider = create_provider(provider_args)
        if not provider:
            print(f"Skipping provider {provider_name} due to missing provider definition")
            return False
        if not provider.test(**provider_args):
            print(f"Skipping provider {provider_name} due to failed test()")
            return False
        await provider.load()
        return await check_provider_model(provider, model)

    async def test_check_models(self):
        # check_models = {"groq": "openai/gpt-oss-20b"}
        for provider_name, model in check_models.items():
            print(f"Checking model {model} for provider {provider_name}...")
            self.assertTrue(await check_provider_model(provider_name, model))

    async def test_groq(self):
        self.assertTrue(await self.check_provider_model("groq", "openai/gpt-oss-20b"))

    async def test_codestral(self):
        self.assertTrue(await self.check_provider_model("codestral", "codestral-latest"))

    async def test_github_copilot(self):
        self.assertTrue(await self.check_provider_model("github-copilot", "gpt-5-mini"))

    async def test_github_models(self):
        self.assertTrue(await self.check_provider_model("github-models", "microsoft/phi-4"))

    async def test_zai_coding_plan(self):
        self.assertTrue(await self.check_provider_model("zai-coding-plan", "glm-4.5-flash"))

    async def test_minimax(self):
        self.assertTrue(await self.check_provider_model("minimax", "MiniMax-M2"))

    async def test_openrouter_free(self):
        self.assertTrue(await self.check_provider_model("openrouter_free", "moonshotai/kimi-k2:free"))

    async def test_ollama(self):
        self.assertTrue(await self.check_provider_model("ollama", "ministral-3-8b"))

    async def test_lmstudio(self):
        self.assertTrue(await self.check_provider_model("lmstudio", "mistralai/ministral-3-3b"))

    async def test_google(self):
        self.assertTrue(await self.check_provider_model("google", "gemini-flash-lite-latest"))

    async def test_anthropic(self):
        self.assertTrue(await self.check_provider_model("anthropic", "claude-3-haiku-20240307"))

    async def test_openai(self):
        self.assertTrue(await self.check_provider_model("openai", "gpt-4.1-nano"))

    async def test_xai(self):
        self.assertTrue(await self.check_provider_model("xai", "grok-4-fast"))

    async def test_alibaba(self):
        self.assertTrue(await self.check_provider_model("alibaba", "qwen-turbo"))

    async def test_zai(self):
        self.assertTrue(await self.check_provider_model("zai", "glm-4.5-flash"))

    async def test_mistral(self):
        self.assertTrue(await self.check_provider_model("mistral", "ministral-3b-latest"))

    async def test_chutes(self):
        self.assertTrue(await self.check_provider_model("chutes", "openai/gpt-oss-20b"))

    async def test_deepseek(self):
        self.assertTrue(await self.check_provider_model("deepseek", "deepseek-chat"))

    async def test_moonshotai(self):
        self.assertTrue(await self.check_provider_model("moonshotai", "kimi-k2-thinking"))

    async def test_nvidia(self):
        self.assertTrue(await self.check_provider_model("nvidia", "nvidia/nvidia-nemotron-nano-9b-v2"))

    async def test_huggingface(self):
        self.assertTrue(await self.check_provider_model("huggingface", "MiniMaxAI/MiniMax-M2"))

    async def test_fireworks_ai(self):
        self.assertTrue(await self.check_provider_model("fireworks_ai", "accounts/fireworks/models/gpt-oss-20b"))
