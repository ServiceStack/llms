#!/usr/bin/env python3
"""
Unit tests for utility functions in llms.main module.
"""

import json
import os
import sys
import unittest

# from dotenv import load_dotenv

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.main import (  # noqa: E402
    create_provider,
    create_provider_kwargs,
    load_config,
)

# Load environment variables from .env file
# load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

config_path = os.path.join(os.path.dirname(__file__), "..", "llms", "llms.json")
with open(config_path) as f:
    config = json.load(f)
providers_path = os.path.join(os.path.dirname(__file__), "..", "llms", "providers.json")
with open(providers_path) as f:
    providers = json.load(f)


class TestProviderConfiguration(unittest.TestCase):
    """Test Provider."""

    def setUp(self):
        load_config(config, providers, verbose=True)

    def test_print_api_keys(self):
        print()
        for provider_name, provider in config["providers"].items():
            definition = create_provider_kwargs(config["providers"][provider_name], providers[provider_name])
            if "env" in definition:
                env_var = definition["env"][0]
                env_value = os.getenv(env_var)
                if env_value:
                    print(f"Checking API key for {provider_name}: {env_var}={env_value}")
                else:
                    print(f"NO API_KEY found for {provider_name}: {env_var}")
                # self.assertTrue(os.getenv(definition["env"][0]) is not None)

    def test_groq_provider_model(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["groq"], providers["groq"]))
        self.assertEqual(
            provider.provider_model("openai/gpt-oss-20b"), "openai/gpt-oss-20b", "Can select model using full name"
        )
        self.assertEqual(
            provider.provider_model("gpt-oss-20b"), "openai/gpt-oss-20b", "Can select model using short name"
        )
        self.assertEqual(provider.provider_model("GPT OSS 20B"), "openai/gpt-oss-20b", "Can select model using name")

    def test_minimax_provider_model(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["minimax"], providers["minimax"]))
        self.assertEqual(
            provider.provider_model("MiniMaxAI/MiniMax-M2"), "MiniMax-M2", "Can select model using full name"
        )
        self.assertEqual(provider.provider_model("MiniMax-M2"), "MiniMax-M2", "Can select model using short name")
        self.assertEqual(
            provider.provider_model("minimaxai/minimax-m2"),
            "MiniMax-M2",
            "Can select model using full name (case-insensitive)",
        )
        self.assertEqual(
            provider.provider_model("minimax-m2"),
            "MiniMax-M2",
            "Can select model using short name (case-insensitive)",
        )

    def test_chutes_provider_model(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["chutes"], providers["chutes"]))
        self.assertEqual(
            provider.provider_model("MiniMaxAI/MiniMax-M2"), "MiniMaxAI/MiniMax-M2", "Can select model using full name"
        )
        self.assertEqual(
            provider.provider_model("MiniMax-M2"), "MiniMaxAI/MiniMax-M2", "Can select model using short name"
        )
        self.assertEqual(
            provider.provider_model("minimaxai/minimax-m2"),
            "MiniMaxAI/MiniMax-M2",
            "Can select model using full name (case-insensitive)",
        )
        self.assertEqual(
            provider.provider_model("minimax-m2"),
            "MiniMaxAI/MiniMax-M2",
            "Can select model using short name (case-insensitive)",
        )

    def test_openrouter_provider_model(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["openrouter"], providers["openrouter"]))
        self.assertEqual(
            provider.provider_model("openai/gpt-oss-20b"), "openai/gpt-oss-20b", "Can select model using full name"
        )
        self.assertEqual(
            provider.provider_model("gpt-oss-20b"), "openai/gpt-oss-20b", "Can select model using short name"
        )
        self.assertEqual(provider.provider_model("GPT OSS 20B"), "openai/gpt-oss-20b", "Can select model using name")

    def test_groq(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["groq"], providers["groq"]))
        self.assertEqual(provider.__class__.__name__, "GroqProvider")
        self.assertEqual(len(provider.models), 9)

    def test_codestral(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["codestral"]))
        self.assertEqual(provider.__class__.__name__, "CodestralProvider")
        self.assertEqual(len(provider.models), 1)

    def test_github_copilot(self):
        provider = create_provider(
            create_provider_kwargs(config["providers"]["github-copilot"], providers["github-copilot"])
        )
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 26)

    def test_github_models(self):
        provider = create_provider(
            create_provider_kwargs(config["providers"]["github-models"], providers["github-models"])
        )
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 55)

    def test_zai_coding_plan(self):
        provider = create_provider(
            create_provider_kwargs(config["providers"]["zai-coding-plan"], providers["zai-coding-plan"])
        )
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 5)

    def test_minimax(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["minimax"], providers["minimax"]))
        self.assertEqual(provider.__class__.__name__, "AnthropicProvider")
        self.assertEqual(len(provider.models), 1)

    def test_openrouter_free(self):
        provider = create_provider(
            create_provider_kwargs(config["providers"]["openrouter_free"], providers["openrouter"])
        )
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 31)

    def test_ollama(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["ollama"]))
        self.assertEqual(provider.__class__.__name__, "OllamaProvider")
        self.assertEqual(len(provider.models), 0)

    def test_lmstudio(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["lmstudio"], providers["lmstudio"]))
        self.assertEqual(provider.__class__.__name__, "LMStudioProvider")
        self.assertEqual(len(provider.models), 0)

    def test_google(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["google"], providers["google"]))
        self.assertEqual(provider.__class__.__name__, "GoogleProvider")
        self.assertEqual(len(provider.models), 5)

    def test_anthropic(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["anthropic"], providers["anthropic"]))
        self.assertEqual(provider.__class__.__name__, "AnthropicProvider")
        self.assertEqual(len(provider.models), 21)

    def test_openai(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["openai"], providers["openai"]))
        self.assertEqual(provider.__class__.__name__, "OpenAiProvider")
        self.assertEqual(len(provider.models), 36)

    def test_xai(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["xai"], providers["xai"]))
        self.assertEqual(provider.__class__.__name__, "XaiProvider")
        self.assertEqual(len(provider.models), 22)

    def test_alibaba(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["alibaba"], providers["alibaba"]))
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 17)

    def test_zai(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["zai"], providers["zai"]))
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 5)

    def test_mistral(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["mistral"], providers["mistral"]))
        self.assertEqual(provider.__class__.__name__, "MistralProvider")
        self.assertEqual(len(provider.models), 19)

    def test_chutes(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["chutes"], providers["chutes"]))
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 54)

    def test_deepseek(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["deepseek"], providers["deepseek"]))
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 2)

    def test_moonshotai(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["moonshotai"], providers["moonshotai"]))
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 5)

    def test_nvidia(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["nvidia"], providers["nvidia"]))
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 21)

    def test_huggingface(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["huggingface"], providers["huggingface"]))
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 14)

    def test_fireworks_ai(self):
        provider = create_provider(
            create_provider_kwargs(config["providers"]["fireworks-ai"], providers["fireworks-ai"])
        )
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 12)

    def test_openrouter(self):
        provider = create_provider(create_provider_kwargs(config["providers"]["openrouter"], providers["openrouter"]))
        self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
        self.assertEqual(len(provider.models), 96)


if __name__ == "__main__":
    unittest.main()
