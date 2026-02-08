# Voice Input Extension

Adds voice-to-text transcription to the chat interface via a microphone button or keyboard shortcut.

## Configuration

Set the `LLMS_VOICE` environment variable to configure which transcription modes are available and in what priority order:

```bash
export LLMS_VOICE="voxtype,transcribe,voxtral-mini-latest"
```

The extension tries each mode in order and uses the first one that's available. The default order is `voxtype,transcribe,voxtral-mini-latest`.

## Available Modes

### voxtype

Uses the [voxtype.io](https://voxtype.io) CLI tool for local transcription.

**Requirements:**
- `voxtype` must be installed and on your `$PATH`
- `ffmpeg` must be installed for audio format conversion

### transcribe

Uses a custom `transcribe` executable for flexible local transcription. This lets you integrate any speech-to-text tool.

**Requirements:**
- A `transcribe` executable on your `$PATH` that accepts an audio wav file and outputs text to stdout
- `ffmpeg` must be installed for audio format conversion

**Interface:**
```bash
transcribe recording.wav > transcript.txt
```

See [Creating a transcribe Script](#creating-a-transcribe-script) for implementation examples.

### voxtral-mini-latest

Uses [Mistral's Voxtral model](https://docs.mistral.ai/models/voxtral-mini-transcribe-26-02) for cloud-based transcription.

**Requirements:**
- Mistral provider must be enabled in your configuration
- `MISTRAL_API_KEY` environment variable must be set

**Pricing:** ~$0.003/minute

## Usage

### Microphone Button

Click the microphone icon in the chat input area to start recording. Click again to stop and transcribe.

### Keyboard Shortcut

**Alt+D** toggles voice recording with two modes:

- **Tap (< 500ms):** Toggle mode — starts recording, press again to stop
- **Hold (≥ 500ms):** Push-to-talk — records while held, stops when released

The transcribed text is appended to the current message input.

---

## Creating a transcribe Script

### Using OpenAI Whisper

Create a script using [uvx](https://github.com/astral-sh/uv) and [openai-whisper](https://github.com/openai/whisper):

```bash
#!/usr/bin/env bash
uvx --from openai-whisper whisper "$1" --model base.en --output_format txt --output_dir /tmp >/dev/null 2>&1

BASENAME=$(basename "${1%.*}")
cat "/tmp/${BASENAME}.txt"
rm -f "/tmp/${BASENAME}.txt"
```

### Using Whisper.cpp

[whisper.cpp](https://github.com/ggml-org/whisper.cpp) provides a faster, dependency-free C++ implementation.

**Setup:**

```bash
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp

# Download a model
sh ./models/download-ggml-model.sh base.en

# Build
cmake -B build
cmake --build build -j --config Release

# Test
./build/bin/whisper-cli -f samples/jfk.wav
```

**Create the transcribe script:**

```bash
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
MODEL="$SCRIPT_DIR/models/ggml-base.en.bin"
CLI="$SCRIPT_DIR/build/bin/whisper-cli"
TMPFILE=$(mktemp /tmp/whisper-XXXXXX)

trap 'rm -f "$TMPFILE" "${TMPFILE}.txt"' EXIT

"$CLI" -m "$MODEL" -otxt -f "$1" -of "$TMPFILE" >/dev/null 2>&1

cat "${TMPFILE}.txt"
```

### Installation

Make the script executable and add it to your `$PATH`:

```bash
chmod +x ./transcribe
sudo ln -s $(pwd)/transcribe /usr/local/bin/transcribe
```
