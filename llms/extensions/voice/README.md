# Voice Input Extension

Adds voice-to-text transcription to the chat interface via a microphone button or keyboard shortcut.

## Configuration

Set the `LLMS_VOICE` environment variable to configure which transcription modes are available and in what priority order:

```bash
export LLMS_VOICE="voxtype,transcribe,api,voxtral-mini-latest"
```

The extension tries each mode in order and uses the first one that's available. The default order is `voxtype,transcribe,api,voxtral-mini-latest`, so local tools are preferred over hosted APIs.

Set `LLMS_VOICE=""` to disable voice input entirely.

## Available Modes

### api

Posts the recording to any OpenAI-compatible `/v1/audio/transcriptions` endpoint. Needs nothing installed.

**Requirements:**
- An API key for a supported provider, or a `url` and `model` for your own endpoint

llms.py ships with `mistral` / `voxtral-mini-latest` configured in `defaults.voice`. If that
provider has no API key it falls back to any other provider that does, so the shipped default
never disables voice input for someone using a different provider.

> **Audio format.** Browsers record `webm/opus`, which Groq and OpenAI accept but Mistral rejects
> with *"Audio input could not be decoded"*. The chat UI converts the recording to 16 kHz mono WAV
> before uploading, so every provider works with no extra software. If the browser can't do the
> conversion the server falls back to `ffmpeg` when it's installed, and otherwise sends the
> original — in which case use `groq` or `openai`, which decode `webm` directly.

Configured under `defaults.voice` in `llms.json`:

```json
{
  "defaults": {
    "voice": {
      "provider": "groq",
      "model": "whisper-large-v3",
      "language": "en"
    }
  }
}
```

| Setting | Purpose |
| --- | --- |
| `provider` | `groq`, `openai` or `mistral` — selects the endpoint and default model |
| `model` | Model id |
| `url` | Full endpoint URL; set instead of `provider` for any other server |
| `api_key` | API key. Prefer `$SOME_VAR` over a literal key |
| `language` | ISO-639-1 hint, e.g. `en`. Omit to auto-detect |
| `prompt` | Biasing prompt for names and jargon |

With no `defaults.voice` section it falls back to the first provider key it finds:

| Environment variable | Provider | Default model |
| --- | --- | --- |
| `GROQ_API_KEY` | Groq | `whisper-large-v3-turbo` |
| `OPENAI_API_KEY` | OpenAI | `whisper-1` |
| `MISTRAL_API_KEY` | Mistral | `voxtral-mini-latest` |

A local server needs no key:

```json
{
  "defaults": {
    "voice": {
      "url": "http://localhost:8001/v1/audio/transcriptions",
      "model": "Systran/faster-whisper-small"
    }
  }
}
```

Each setting can be overridden by an environment variable, which takes precedence over `llms.json`: `LLMS_TRANSCRIBE_PROVIDER`, `LLMS_TRANSCRIBE_MODEL`, `LLMS_TRANSCRIBE_URL`, `LLMS_TRANSCRIBE_KEY`, `LLMS_TRANSCRIBE_LANG`, `LLMS_TRANSCRIBE_PROMPT`.

### voxtype

Uses the [voxtype.io](https://voxtype.io) CLI tool for local transcription. Requires a graphical desktop session, so it isn't available in headless or containerised deployments.

**Requirements:**
- `voxtype` must be installed and on your `$PATH`
- `ffmpeg` must be installed for audio format conversion

### transcribe

Uses a custom `transcribe` executable for flexible local transcription. This lets you integrate any speech-to-text tool.

**Requirements:**
- A `transcribe` executable on your `$PATH` that accepts an audio wav file and outputs text to stdout
- `ffmpeg` must be installed for audio format conversion

### voxtral-mini-latest

Uses Mistral's Voxtral model through the configured Mistral provider. Any `voxtral*` model id works, e.g. `LLMS_VOICE="voxtral-small-latest"`.

**Requirements:**
- Mistral provider must be enabled in your configuration
- `MISTRAL_API_KEY` environment variable must be set

## Troubleshooting

Run with `--verbose` to see which mode was chosen and why the others were skipped.

If the microphone button doesn't appear at all, check the browser is on a secure origin — `getUserMedia` is only available over HTTPS or on `localhost`/`127.0.0.1`.
