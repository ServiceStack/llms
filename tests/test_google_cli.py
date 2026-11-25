"""Tests for GoogleProviderCli (@ai-sdk/google-cli) using the local agy CLI."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llms.extensions.providers.google import install_google


class _FakeStdin:
    def __init__(self):
        self.buffer = bytearray()
        self.closed = False

    def write(self, data):
        self.buffer.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class _FakeStdout:
    def __init__(self, lines):
        self._lines = [line.encode("utf-8") if isinstance(line, str) else line for line in lines]

    def __aiter__(self):
        self._iter = iter(self._lines)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeStderr:
    def __init__(self, text=""):
        self._data = text.encode("utf-8")

    async def read(self):
        return self._data


class _FakeProcess:
    def __init__(self, stdout_lines=None, stderr_text="", returncode=0, communicate_stdout=b""):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_lines or [])
        self.stderr = _FakeStderr(stderr_text)
        self.returncode = returncode
        self._communicate_stdout = communicate_stdout
        self._stderr_bytes = stderr_text.encode("utf-8")
        self.terminated = False

    async def communicate(self, input=None):
        return self._communicate_stdout, self._stderr_bytes

    async def wait(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class GoogleCliProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.providers = []
        self.ctx = SimpleNamespace(
            add_provider=lambda p: self.providers.append(p),
            log=lambda msg: None,
            dbg=lambda msg: None,
            log_json=lambda obj: obj,
            threads=None,
        )
        install_google(self.ctx)
        self.cli_cls = next(p for p in self.providers if p.sdk == "@ai-sdk/google-cli")

    def _make_provider(self, **kwargs):
        defaults = {
            "id": "google",
            "name": "Google",
            "models": {
                "gemini-4-neon": {
                    "id": "gemini-4-neon",
                    "name": "Gemini 4 Neon",
                    "tool_call": True,
                    "cost": {"input": 1.0, "output": 2.0},
                },
                "gemini-3.8-flash": {
                    "id": "gemini-3.8-flash",
                    "name": "Gemini 3.8 Flash",
                    "tool_call": True,
                    "cost": {"input": 0.1, "output": 0.2},
                },
            },
        }
        defaults.update(kwargs)
        return self.cli_cls(**defaults)

    def test_validate_requires_agy_in_path(self):
        provider = self._make_provider()
        self.assertFalse(provider.models["gemini-4-neon"]["tool_call"])
        with patch("llms.extensions.providers.google.shutil.which", return_value=None):
            self.assertIn("agy", provider.validate())
        with patch("llms.extensions.providers.google.shutil.which", return_value="/usr/local/bin/agy"):
            self.assertIsNone(provider.validate())

    async def test_load_discovers_models_from_agy_models(self):
        provider = self._make_provider()
        raw_output = (
            "\r\x1b[36m⠋ Fetching available models...\x1b[0m\r"
            "gemini-4-neon-high\tGemini 4 Neon (High)\n"
            "gemini-4-neon-medium           Gemini 4 Neon (Medium)\n"
            "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
        ).encode("utf-8")
        proc = _FakeProcess(communicate_stdout=raw_output, returncode=0)

        with (
            patch("llms.extensions.providers.google.shutil.which", return_value="/usr/local/bin/agy"),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            await provider.load()

        mock_exec.assert_called_once()
        self.assertEqual(mock_exec.call_args.args, ("agy", "models"))
        self.assertIn("gemini-4-neon-high", provider.models)
        self.assertIn("gemini-3.8-flash-low", provider.models)
        self.assertEqual(provider.models["gemini-4-neon-high"]["name"], "Gemini 4 Neon (High)")
        self.assertFalse(provider.models["gemini-4-neon-high"]["tool_call"])
        self.assertEqual(provider.models["gemini-4-neon-high"]["cost"], {"input": 0, "output": 0})
        self.assertEqual(provider.provider_model("gemini-4-neon"), "gemini-4-neon")
        self.assertEqual(provider.provider_model("Gemini 4 Neon (High)"), "gemini-4-neon-high")

    async def test_chat_streams_and_maps_response(self):
        provider = self._make_provider()
        events = [
            json.dumps({"event": "init", "conversation_id": "conv-123", "init": {}}) + "\n",
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 1,
                        "state": "ACTIVE",
                        "step_type": "agent_response",
                        "text_delta": "Hello ",
                    },
                }
            )
            + "\n",
            json.dumps(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 1,
                        "state": "DONE",
                        "step_type": "agent_response",
                        "text_delta": "world!\n",
                    },
                }
            )
            + "\n",
            json.dumps(
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "conv-123",
                        "status": "SUCCESS",
                        "response": "Hello world!\n",
                        "duration_seconds": 1.25,
                        "num_turns": 1,
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 12,
                            "thinking_tokens": 5,
                            "cache_read_tokens": 40,
                            "total_tokens": 112,
                        },
                    },
                }
            )
            + "\n",
        ]
        proc = _FakeProcess(stdout_lines=events, returncode=0)

        chat = {
            "model": "gemini-4-neon",
            "reasoning": {"reasoning_effort": "low"},
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "First"},
                {"role": "assistant", "content": "Ack"},
                {"role": "user", "content": "Say hello"},
            ],
        }
        context = {"nostore": True}

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await provider.chat(chat, context=context)

        cmd = list(mock_exec.call_args.args)
        self.assertEqual(
            cmd,
            [
                "agy",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--disable-slash-commands",
                "--model",
                "gemini-4-neon",
                "--effort",
                "low",
            ],
        )
        sent = json.loads(proc.stdin.buffer.decode("utf-8"))
        self.assertEqual(sent["event"], "user")
        self.assertIn("System instructions:\nBe brief.", sent["message"]["content"])
        self.assertIn("Previous conversation:\n[user]: First\n\n[assistant]: Ack", sent["message"]["content"])
        self.assertIn("Say hello", sent["message"]["content"])

        self.assertEqual(result["id"], "conv-123")
        self.assertEqual(result["choices"][0]["message"]["content"], "Hello world!")
        self.assertEqual(result["choices"][0]["finish_reason"], "stop")
        self.assertEqual(result["usage"]["prompt_tokens"], 100)
        self.assertEqual(result["usage"]["completion_tokens"], 12)
        self.assertEqual(result["usage"]["total_tokens"], 112)
        self.assertEqual(result["usage"]["prompt_tokens_details"]["cached_tokens"], 40)
        self.assertEqual(result["usage"]["completion_tokens_details"]["reasoning_tokens"], 5)
        self.assertEqual(result["metadata"]["duration"], 1250)
        self.assertEqual(result["metadata"]["pricing"], "0/0")

    async def test_chat_omits_effort_when_model_slug_already_has_effort(self):
        provider = self._make_provider()
        events = [
            json.dumps(
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "conv-456",
                        "status": "SUCCESS",
                        "response": "2\n",
                        "duration_seconds": 0.5,
                        "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
                    },
                }
            )
            + "\n"
        ]
        proc = _FakeProcess(stdout_lines=events, returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await provider.chat(
                {
                    "model": "gemini-4-neon-high",
                    "reasoning_effort": "low",
                    "messages": [{"role": "user", "content": "1+1="}],
                },
                context={"nostore": True},
            )

        cmd = list(mock_exec.call_args.args)
        self.assertNotIn("--effort", cmd)
        self.assertIn("gemini-4-neon-high", cmd)

    async def test_chat_raises_on_error_result(self):
        provider = self._make_provider()
        events = [
            json.dumps(
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "",
                        "status": "ERROR",
                        "response": "",
                        "error": "invalid model selection",
                    },
                }
            )
            + "\n"
        ]
        proc = _FakeProcess(stdout_lines=events, returncode=1)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with self.assertRaisesRegex(Exception, "invalid model selection"):
                await provider.chat(
                    {"model": "bad-model", "messages": [{"role": "user", "content": "hi"}]},
                    context={"nostore": True},
                )


if __name__ == "__main__":
    unittest.main()
