import json
import os
import re
import shutil

import aiohttp
from aiohttp import web

LLMS_VOICE = os.getenv("LLMS_VOICE", "voxtype,transcribe,api,voxtral-mini-latest")

# provider id -> (api key env var, endpoint, default model)
PROVIDERS = {
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3-turbo"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/audio/transcriptions", "whisper-1"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1/audio/transcriptions", "voxtral-mini-latest"),
}

# The browser records webm; mimetypes.guess_type() calls that video/webm, which
# some transcription APIs reject. Map the formats these endpoints accept.
AUDIO_TYPES = {
    ".webm": "audio/webm",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
}


# Formats the transcription APIs reliably decode. The browser records webm/opus,
# which Groq and OpenAI accept but Mistral rejects with "Audio input could not be
# decoded", so anything outside this set is converted to WAV first when ffmpeg is
# available.
PORTABLE_FORMATS = (".wav", ".mp3", ".mpga", ".m4a", ".mp4", ".flac", ".ogg", ".oga")

# Where ffmpeg usually lives. A process launched from a GUI, a service manager or a
# sanitised environment often doesn't inherit Homebrew's PATH, so `which` alone
# misses an ffmpeg the user definitely has installed.
FFMPEG_PATHS = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "/snap/bin/ffmpeg")


def which_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    for path in FFMPEG_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def audio_content_type(filename):
    ext = os.path.splitext(filename or "")[1].lower()
    return AUDIO_TYPES.get(ext, "application/octet-stream")


def to_portable_audio(ctx, audio_bytes, filename):
    """
    Convert to 16 kHz mono WAV when the recording is in a format some providers
    can't decode. Returns (bytes, filename, converted). Sends the original
    unchanged when ffmpeg isn't installed or the conversion fails.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in PORTABLE_FORMATS:
        return audio_bytes, filename, False, ""
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        reason = "ffmpeg not found on PATH"
        ctx.log(f"{reason}, sending {ext or 'audio'} unconverted")
        return audio_bytes, filename, False, reason

    import tempfile

    temp_in = temp_out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext or ".webm", delete=False) as f:
            f.write(audio_bytes)
            temp_in = f.name
        temp_out = temp_in + ".wav"
        ctx.run_command([ffmpeg, "-i", temp_in, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", temp_out, "-y"])
        with open(temp_out, "rb") as f:
            converted_bytes = f.read()
        wav_name = os.path.splitext(os.path.basename(filename or "audio"))[0] + ".wav"
        ctx.dbg(f"converted {ext} ({len(audio_bytes)} bytes) to wav ({len(converted_bytes)} bytes) with {ffmpeg}")
        return converted_bytes, wav_name, True, ""
    except Exception as e:  # noqa: BLE001
        reason = f"ffmpeg conversion failed ({e})"
        ctx.log(f"{reason}, sending original audio")
        return audio_bytes, filename, False, reason
    finally:
        for path in (temp_in, temp_out):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


def expand_env(value):
    """A leading $ reads an environment variable, as api_key does elsewhere in llms.json."""
    if isinstance(value, str) and value.startswith("$"):
        return os.getenv(value[1:], "")
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def resolve_api_config(ctx):
    """
    Work out the endpoint, model and key for the `api` mode.

    Precedence: LLMS_TRANSCRIBE_* environment > defaults.voice in llms.json >
    auto-detection from whichever provider API key is present. Returns None when
    nothing is configured, which makes the mode unavailable.

    A configured provider whose API key is missing falls back to any other
    provider that does have one, so the shipped default doesn't disable voice
    input for someone using a different provider.
    """
    defaults = ctx.config.get("defaults") if isinstance(ctx.config, dict) else None
    voice = defaults.get("voice") if isinstance(defaults, dict) else None
    if not isinstance(voice, dict):
        voice = {}

    def setting(env_var, key):
        val = os.getenv(env_var, "").strip()
        if val:
            return val, "env"
        val = expand_env(voice.get(key, "")).strip()
        if val:
            return val, "llms.json"
        return "", ""

    url, url_src = setting("LLMS_TRANSCRIBE_URL", "url")
    model, model_src = setting("LLMS_TRANSCRIBE_MODEL", "model")
    key, key_src = setting("LLMS_TRANSCRIBE_KEY", "api_key")
    want, want_src = setting("LLMS_TRANSCRIBE_PROVIDER", "provider")
    language, _ = setting("LLMS_TRANSCRIBE_LANG", "language")
    prompt, _ = setting("LLMS_TRANSCRIBE_PROMPT", "prompt")

    def first_provider_with_key():
        for provider_id, (env_var, _u, _m) in PROVIDERS.items():
            if os.getenv(env_var, "").strip():
                return provider_id
        return None

    name = None
    src = ""
    if want:
        if want not in PROVIDERS:
            ctx.log(f"Unknown voice provider '{want}' (from {want_src}), known: {', '.join(PROVIDERS)}")
        elif key or os.getenv(PROVIDERS[want][0], "").strip():
            name, src = want, want_src
        else:
            ctx.dbg(f"Voice provider '{want}' has no {PROVIDERS[want][0]}, looking for another")

        if not name:
            # Fall back rather than lose voice input, e.g. the shipped default names
            # mistral but this user only has a Groq key.
            name = first_provider_with_key()
            if name:
                src = "fallback"
                # the configured model and url belonged to the provider we skipped
                if model_src == "llms.json":
                    model, model_src = "", ""
                if url_src == "llms.json":
                    url, url_src = "", ""
    elif not url:
        # Only auto-detect when no explicit endpoint was given.
        name = first_provider_with_key()
        if name:
            src = "auto"

    if name:
        env_var, default_url, default_model = PROVIDERS[name]
        url = url or default_url
        model = model or default_model
        key = key or os.getenv(env_var, "").strip()
        if not key:
            ctx.dbg(f"Cannot use api - voice provider '{name}' selected ({src}) but {env_var} is not set")
            return None
    else:
        # A local server (speaches, faster-whisper-server, ...) usually needs no key.
        if not url:
            ctx.dbg("Cannot use api - no voice provider configured, see defaults.voice in llms.json")
            return None
        if not model:
            ctx.dbg("Cannot use api - a voice url is configured but no model")
            return None
        name = "custom"

    return {
        "provider": name,
        "url": url,
        "model": model,
        "api_key": key,
        "language": language,
        "prompt": prompt,
        "sources": {
            "provider": src or "url",
            "url": url_src or "default",
            "model": model_src or "default",
            "key": key_src or "provider env",
        },
    }


def install(ctx):
    voice_options = [opt.strip() for opt in LLMS_VOICE.split(",") if opt.strip()]
    mode = None
    api_config = None

    for opt in voice_options:
        if opt == "voxtype":
            if not shutil.which("voxtype"):
                ctx.dbg(f"Cannot use {opt} - voxtype not installed")
            else:
                mode = opt
                break
        elif opt == "transcribe":
            if not shutil.which("transcribe"):
                ctx.dbg(f"Cannot use {opt} - transcribe not installed")
            else:
                mode = opt
                break
        elif opt == "api":
            api_config = resolve_api_config(ctx)
            if api_config:
                mode = opt
                break
        elif opt.startswith("voxtral"):
            mistral = ctx.config.get("providers", {}).get("mistral")
            if not mistral or not mistral.get("enabled") or not os.getenv("MISTRAL_API_KEY"):
                ctx.dbg(f"Cannot use {opt} - Mistral not enabled")
            else:
                mode = opt
                break
        else:
            ctx.dbg(f"Cannot use {opt} - unknown voice mode")

    if (mode == "transcribe" or mode == "voxtype") and not which_ffmpeg():
        ctx.dbg(f"Cannot use {mode} - ffmpeg not installed")
        mode = None

    if not mode:
        ctx.disabled = True
        return

    if mode == "api":
        src = api_config["sources"]
        ctx.log(
            f"Using api for voice: {api_config['provider']} [{src['provider']}] "
            f"model={api_config['model']} [{src['model']}]"
        )
        ctx.dbg(f"Voice endpoint: {api_config['url']} [{src['url']}]")
    else:
        ctx.log(f"Using {mode} for voice")

    async def transcribe_api(audio_bytes, filename):
        """POST to any OpenAI-compatible /v1/audio/transcriptions endpoint."""
        audio_bytes, filename, converted, convert_error = to_portable_audio(ctx, audio_bytes, filename)

        data = aiohttp.FormData()
        data.add_field("model", api_config["model"])
        data.add_field("response_format", "json")
        if api_config["language"]:
            data.add_field("language", api_config["language"])
        if api_config["prompt"]:
            data.add_field("prompt", api_config["prompt"])
        data.add_field("file", audio_bytes, filename=filename, content_type=audio_content_type(filename))

        headers = {}
        if api_config["api_key"]:
            headers["Authorization"] = f"Bearer {api_config['api_key']}"
            # Mistral's transcription endpoint also accepts x-api-key; send both,
            # matching what the mistral provider does.
            if api_config["provider"] == "mistral":
                headers["x-api-key"] = api_config["api_key"]

        ctx.dbg(f"POST {api_config['url']} model={api_config['model']} file={filename} ({len(audio_bytes)} bytes)")

        async with aiohttp.ClientSession(timeout=ctx.get_client_timeout()) as session, session.post(
            api_config["url"], headers=headers, data=data
        ) as response:
            body = await response.text()
            if response.status != 200:
                message = f"{api_config['provider']} returned {response.status}: {body[:500]}"
                if not converted and "decod" in body.lower():
                    message += (
                        f"\n{api_config['provider']} could not decode this recording"
                        f" ({os.path.splitext(filename)[1] or 'unknown format'})"
                        f" and it was not converted: {convert_error or 'already a portable format'}."
                        " Newer browsers convert to WAV before uploading; otherwise install ffmpeg,"
                        " or use a provider that accepts this format (groq, openai)."
                    )
                raise Exception(message)
            try:
                result = json.loads(body)
            except ValueError:
                raise Exception(f"{api_config['provider']} returned a non-JSON response: {body[:300]}")  # noqa: B904

        text = result.get("text")
        if text is None:
            segments = result.get("segments") or []
            text = "".join(s.get("text", "") for s in segments if isinstance(s, dict))
        return (text or "").strip()

    async def transcribe_audio(request):
        """
        Transcribe recorded audio
        POST /transcribe
        """
        # Get audio data from request
        data = await request.post()
        audio_file = data.get("file")

        if not audio_file:
            raise Exception("No audio file provided")

        # Read audio data
        audio_bytes = audio_file.file.read()

        # Container magic, so a truncated or mangled upload is obvious in the log:
        # 1a45dfa3 = webm/matroska, 52494646 = RIFF/wav, 000000..66747970 = mp4
        ctx.dbg(
            f"/transcribe received {audio_file.filename!r} {len(audio_bytes)} bytes"
            f" type={getattr(audio_file, 'content_type', '?')} magic={audio_bytes[:12].hex()}"
        )
        if os.getenv("LLMS_VOICE_DUMP") == "1":
            dump = os.path.join(
                os.getenv("LLMS_HOME", os.path.join(os.getenv("HOME", "."), ".llms")),
                "voice-upload" + (os.path.splitext(audio_file.filename or "")[1] or ".bin"),
            )
            try:
                with open(dump, "wb") as f:
                    f.write(audio_bytes)
                ctx.log(f"wrote the raw upload to {dump} for inspection")
            except OSError as e:
                ctx.dbg(f"could not write {dump}: {e}")

        if mode == "api":
            text = await transcribe_api(audio_bytes, audio_file.filename)
            return web.json_response({"text": text, "mode": mode, "model": api_config["model"]})

        if mode.startswith("voxtral"):
            # Mistral can't decode the browser's webm, so convert here too
            voxtral_bytes, voxtral_name, _, _ = to_portable_audio(ctx, audio_bytes, audio_file.filename)
            mistral = ctx.get_registered_provider("mistral")
            result = await mistral.transcription.transcribe(voxtral_bytes, voxtral_name, model=mode)
            result["mode"] = mode
            return web.json_response(result)

        # Save to temporary file for voxtype
        import tempfile
        from pathlib import Path

        suffix = Path(audio_file.filename).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_input:
            temp_input.write(audio_bytes)
            temp_input_path = temp_input.name

        # Convert to 16kHz WAV using ffmpeg
        temp_wav_path = temp_input_path + ".wav"

        try:
            ctx.run_command(
                ["ffmpeg", "-i", temp_input_path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", temp_wav_path, "-y"]
            )

            if mode == "transcribe":
                result = ctx.run_command(["transcribe", temp_wav_path])

                if result.returncode != 0:
                    raise Exception(result.stderr)

                text = result.stdout.decode("utf-8").strip()
                return web.json_response({"text": text, "mode": mode})

            # Run voxtype to transcribe
            result = ctx.run_command(["voxtype", "transcribe", temp_wav_path])

            ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

            # Extract transcription - take the last non-empty line that isn't a log
            output_lines = []
            for line in result.stdout.decode("utf-8").strip().split("\n"):
                clean_line = ansi_escape.sub("", line).strip()
                if clean_line and not clean_line.startswith("[") and "INFO" not in clean_line:
                    output_lines.append(clean_line)

            transcription = output_lines[-1] if output_lines else ""

        finally:
            # Clean up
            if os.path.exists(temp_input_path):
                os.remove(temp_input_path)
            if os.path.exists(temp_wav_path):
                os.remove(temp_wav_path)

        return web.json_response({"text": transcription, "mode": mode})

    ctx.add_post("/transcribe", transcribe_audio)


__install__ = install
