import os
import re
import shutil

from aiohttp import web

LLMS_VOICE = os.getenv("LLMS_VOICE", "voxtype,transcribe,voxtral-mini-latest")


def install(ctx):
    voice_options = LLMS_VOICE.split(",")
    mode = None

    for opt in voice_options:
        if opt == "voxtype":
            if not shutil.which("voxtype"):
                ctx.dbg(f"Cannot use {opt} - voxtype not installed")
            else:
                mode = opt
                break
        if opt == "transcribe":
            if not shutil.which("transcribe"):
                ctx.dbg(f"Cannot use {opt} - transcribe not installed")
            else:
                mode = opt
                break
        elif opt.startswith("voxtral"):
            mistral = ctx.config.get("providers", {}).get("mistral")
            if not mistral or not mistral.get("enabled") or not os.getenv("MISTRAL_API_KEY"):
                ctx.dbg(f"Cannot use {opt} - Mistral not enabled")
            else:
                mode = opt
                break

    if (mode == "transcribe" or mode == "voxtype") and not shutil.which("ffmpeg"):
        ctx.dbg(f"Cannot use {mode} - ffmpeg not installed")
        mode = None

    if not mode:
        ctx.disabled = True
        return

    ctx.log(f"Using {mode} for voice")

    async def transcribe_audio(request):
        """
        Transcribe audio using Voxtral
        POST /transcribe
        """
        # Get audio data from request
        data = await request.post()
        audio_file = data.get("file")

        if not audio_file:
            raise Exception("No audio file provided")

        # Read audio data
        audio_bytes = audio_file.file.read()

        if mode == "voxtral-mini-latest":
            mistral = ctx.get_provider("mistral")
            result = await mistral.transcription.transcribe(audio_bytes, audio_file.filename)
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
