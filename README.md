# ServiceStack LLMs

Lightweight CLI and OpenAI-compatible server for querying multiple Large Language Model (LLM) providers.

Configure additional providers and models in [llms.json](llms.json)
 - Mix and match local models with models from different API providers
 - Requests automatically routed to available providers that supports the requested model (in defined order)
 - Define free/cheapest/local providers first to save on costs
 - Any failures are automatically retried on the next available provider

## Features

- **Lightweight**: Single [llms.py](llms.py) Python file with single `aiohttp` dependency
- **Multi-Provider Support**: OpenRouter, Ollama, Anthropic, Google, OpenAI, Grok, Groq, Qwen, Mistral
- **OpenAI-Compatible API**: Works with any client that supports OpenAI's chat completion API
- **Configuration Management**: Easy provider enable/disable and configuration management
- **CLI Interface**: Simple command-line interface for quick interactions
- **Server Mode**: Run an OpenAI-compatible HTTP server at `http://localhost:{PORT}/v1/chat/completions`
- **Image Support**: Process images through vision-capable models
- **Audio Support**: Process audio through audio-capable models
- **Custom Chat Templates**: Configurable chat completion request templates for different modalities
- **Auto-Discovery**: Automatically discover available Ollama models
- **Unified Models**: Define custom model names that map to different provider-specific names
- **Multi-Model Support**: Support for over 160+ different LLMs

## Installation

### Option 1: Install from PyPI

```bash
pip install llms-py
```

### Option 2: Download directly

1. Download [llms.py](llms.py)

```bash
curl -O https://raw.githubusercontent.com/ServiceStack/llms/main/llms.py
chmod +x llms.py
mv llms.py ~/.local/bin/llms
```

2. Install single dependency:

```bash
pip install aiohttp
```

## Quick Start

### 1. Initialize Configuration

Create a default configuration file:

```bash
llms --init
```

This saves the latest [llms.json](llms.json) configuration to `~/.llms/llms.json`.

Modify `~/.llms/llms.json` to enable providers, add required API keys, additional models or any custom
OpenAI-compatible providers.

### 2. Set API Keys

Set environment variables for the providers you want to use:

```bash
export OPENROUTER_API_KEY="..."
export GROQ_API_KEY="..."
export GOOGLE_API_KEY="..."
export ANTHROPIC_API_KEY="..."
export GROK_API_KEY="..."
export DASHSCOPE_API_KEY="..."
# ... etc
```

### 3. Enable Providers

Enable the providers you want to use:

```bash
# Enable providers with free models and free tiers
llms --enable openrouter_free google_free groq

# Enable paid providers
llms --enable openrouter anthropic google openai mistral grok qwen
```

### 4. Start Chatting

```bash
llms "What is the capital of France?"
```

## Configuration

The configuration file (`llms.json`) defines available providers, models, and default settings. Key sections:

### Defaults
- `headers`: Common HTTP headers for all requests
- `text`: Default chat completion request template for text prompts

### Providers

Each provider configuration includes:
- `enabled`: Whether the provider is active
- `type`: Provider class (OpenAiProvider, GoogleProvider, etc.)
- `api_key`: API key (supports environment variables with `$VAR_NAME`)
- `base_url`: API endpoint URL
- `models`: Model name mappings (local name → provider name)

## Command Line Usage

### Basic Chat

```bash
# Simple question
llms "Explain quantum computing"

# With specific model
llms -m gemini-2.5-pro "Write a Python function to sort a list"
llms -m grok-4 "Explain this code with humor"
llms -m qwen3-max "Translate this to Chinese"

# With system prompt
llms -s "You are a helpful coding assistant" "How do I reverse a string in Python?"

# With image (vision models)
llms --image image.jpg "What's in this image?"
llms --image https://example.com/photo.png "Describe this photo"

# Display full JSON Response
llms "Explain quantum computing" --raw
```

### Using a Chat Template

By default llms uses the `defaults/text` chat completion request defined in [llms.json](llms.json). 

You can instead use a custom chat completion request with `--chat`, e.g:

```bash
# Load chat completion request from JSON file
llms --chat request.json

# Override user message
llms --chat request.json "New user message"

# Override model
llms -m kimi-k2 --chat request.json
```

Example `request.json`:

```json
{
  "model": "kimi-k2",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user",   "content": ""}
  ],
  "temperature": 0.7,
  "max_tokens": 150
}
```

### Image Requests

Send images to vision-capable models using the `--image` option:

```bash
# Use defaults/image Chat Template (Describe the key features of the input image)
llms --image ./screenshot.png

# Local image file
llms --image ./screenshot.png "What's in this image?"

# Remote image URL
llms --image https://example.org/photo.jpg "Describe this photo"

# Data URI
llms --image "data:image/png;base64,$(base64 -w 0 image.png)" "Describe this image"

# With a specific vision model
llms -m gemini-2.5-flash --image chart.png "Analyze this chart"
llms -m qwen2.5vl --image document.jpg "Extract text from this document"

# Combined with system prompt
llms -s "You are a data analyst" --image graph.png "What trends do you see?"

# With custom chat template
llms --chat image-request.json --image photo.jpg
```

Example of `image-request.json`:

```json
{
    "model": "qwen2.5vl",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": ""
                    }
                },
                {
                    "type": "text",
                    "text": "Caption this image"
                }
            ]
        }
    ]
}
```

**Supported image formats**: PNG, WEBP, JPG, JPEG, GIF, BMP, TIFF, ICO

**Image sources**:
- **Local files**: Absolute paths (`/path/to/image.jpg`) or relative paths (`./image.png`, `../image.jpg`)
- **Remote URLs**: HTTP/HTTPS URLs are automatically downloaded
- **Data URIs**: Base64-encoded images (`data:image/png;base64,...`)

Images are automatically processed and converted to base64 data URIs before being sent to the model.

### Vision-Capable Models

Popular models that support image analysis:
- **OpenAI**: GPT-4o, GPT-4o-mini, GPT-4.1
- **Anthropic**: Claude Sonnet 4.0, Claude Opus 4.1
- **Google**: Gemini 2.5 Pro, Gemini Flash
- **Qwen**: Qwen2.5-VL, Qwen3-VL, QVQ-max
- **Ollama**: qwen2.5vl, llava

Images are automatically downloaded and converted to base64 data URIs.

### Audio Requests

Send audio files to audio-capable models using the `--audio` option:

```bash
# Use defaults/audio Chat Template (Transcribe the audio)
llms --audio ./recording.mp3

# Local audio file
llms --audio ./meeting.wav "Summarize this meeting recording"

# Remote audio URL
llms --audio https://example.org/podcast.mp3 "What are the key points discussed?"

# With a specific audio model
llms -m gpt-4o-audio-preview --audio interview.mp3 "Extract the main topics"
llms -m gemini-2.5-flash --audio interview.mp3 "Extract the main topics"

# Combined with system prompt
llms -s "You're a transcription specialist" --audio talk.mp3 "Provide a detailed transcript"

# With custom chat template
llms --chat audio-request.json --audio speech.wav
```

Example of `audio-request.json`:

```json
{
    "model": "gpt-4o-audio-preview",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": "",
                        "format": "mp3"
                    }
                },
                {
                    "type": "text",
                    "text": "Please transcribe this audio"
                }
            ]
        }
    ]
}
```

**Supported audio formats**: MP3, WAV

**Audio sources**:
- **Local files**: Absolute paths (`/path/to/audio.mp3`) or relative paths (`./audio.wav`, `../recording.m4a`)
- **Remote URLs**: HTTP/HTTPS URLs are automatically downloaded
- **Base64 Data**: Base64-encoded audio

Audio files are automatically processed and converted to base64 data before being sent to the model.

### Audio-Capable Models

Popular models that support audio processing:
- **OpenAI**: gpt-4o-audio-preview
- **Google**: gemini-2.5-pro, gemini-2.5-flash, gemini-2.5-flash-lite

Audio files are automatically downloaded and converted to base64 data URIs with appropriate format detection.

## Server Mode

Run as an OpenAI-compatible HTTP server:

```bash
# Start server on port 8000
llms --serve 8000
```

The server exposes a single endpoint:
- `POST /v1/chat/completions` - OpenAI-compatible chat completions

Example client usage:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-k2",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

### Configuration Management

```bash
# List enabled providers and models
llms --list
llms ls

# List specific providers
llms ls ollama
llms ls google anthropic

# Enable providers
llms --enable openrouter
llms --enable anthropic google_free groq

# Disable providers
llms --disable ollama
llms --disable openai anthropic

# Set default model
llms --default grok-4
```

### Update

1. Installed from PyPI

```bash
pip install llms-py --upgrade
```

2. Using Direct Download

```bash
# Update to latest version (Downloads latest llms.py)
llms --update
```

### Advanced Options

```bash
# Use custom config file
llms --config /path/to/config.json "Hello"

# Get raw JSON response
llms --raw "What is 2+2?"

# Enable verbose logging
llms --verbose "Tell me a joke"

# Custom log prefix
llms --verbose --logprefix "[DEBUG] " "Hello world"

# Set default model (updates config file)
llms --default grok-4

# Update llms.py to latest version
llms --update
```

### Default Model Configuration

The `--default MODEL` option allows you to set the default model used for all chat completions. This updates the `defaults.text.model` field in your configuration file:

```bash
# Set default model to gpt-oss
llms --default gpt-oss:120b

# Set default model to Claude Sonnet
llms --default claude-sonnet-4-0

# The model must be available in your enabled providers
llms --default gemini-2.5-pro
```

When you set a default model:
- The configuration file (`~/.llms/llms.json`) is automatically updated
- The specified model becomes the default for all future chat requests
- The model must exist in your currently enabled providers
- You can still override the default using `-m MODEL` for individual requests

### Updating llms.py

The `--update` option downloads and installs the latest version of `llms.py` from the GitHub repository:

```bash
# Update to latest version
llms --update
```

This command:
- Downloads the latest `llms.py` from `https://raw.githubusercontent.com/ServiceStack/llms/refs/heads/main/llms.py`
- Overwrites your current `llms.py` file with the latest version
- Preserves your existing configuration file (`llms.json`)
- Requires an internet connection to download the update

### Beautiful rendered Markdown

Pipe Markdown output to [glow](https://github.com/charmbracelet/glow) to beautifully render it in the terminal:

```bash
llms "Explain quantum computing" | glow
```

## Supported Providers

### OpenAI
- **Type**: `OpenAiProvider`
- **Models**: GPT-5, GPT-5 Codex, GPT-4o, GPT-4o-mini, o3, etc.
- **Features**: Text, images, function calling

```bash
export OPENAI_API_KEY="your-key"
llms --enable openai
```

### Anthropic (Claude)
- **Type**: `OpenAiProvider` 
- **Models**: Claude Opus 4.1, Sonnet 4.0, Haiku 3.5, etc.
- **Features**: Text, images, large context windows

```bash
export ANTHROPIC_API_KEY="your-key"
llms --enable anthropic
```

### Google Gemini
- **Type**: `GoogleProvider`
- **Models**: Gemini 2.5 Pro, Flash, Flash-Lite
- **Features**: Text, images, safety settings

```bash
export GOOGLE_API_KEY="your-key"
llms --enable google_free
```

### Groq
- **Type**: `OpenAiProvider`
- **Models**: Llama 3.3, Gemma 2, Kimi K2, etc.
- **Features**: Fast inference, competitive pricing

```bash
export GROQ_API_KEY="your-key" 
llms --enable groq
```

### Ollama (Local)
- **Type**: `OllamaProvider`
- **Models**: Auto-discovered from local Ollama installation
- **Features**: Local inference, privacy, no API costs

```bash
# Ollama must be running locally
llms --enable ollama
```

### OpenRouter
- **Type**: `OpenAiProvider`
- **Models**: 100+ models from various providers
- **Features**: Access to latest models, free tier available

```bash
export OPENROUTER_API_KEY="your-key"
llms --enable openrouter
```

### Mistral
- **Type**: `OpenAiProvider`
- **Models**: Mistral Large, Codestral, Pixtral, etc.
- **Features**: Code generation, multilingual

```bash
export MISTRAL_API_KEY="your-key"
llms --enable mistral
```

### Grok (X.AI)
- **Type**: `OpenAiProvider`
- **Models**: Grok-4, Grok-3, Grok-3-mini, Grok-code-fast-1, etc.
- **Features**: Real-time information, humor, uncensored responses

```bash
export GROK_API_KEY="your-key"
llms --enable grok
```

### Qwen (Alibaba Cloud)
- **Type**: `OpenAiProvider`
- **Models**: Qwen3-max, Qwen-max, Qwen-plus, Qwen2.5-VL, QwQ-plus, etc.
- **Features**: Multilingual, vision models, coding, reasoning, audio processing

```bash
export DASHSCOPE_API_KEY="your-key"
llms --enable qwen
```

## Model Routing

The tool automatically routes requests to the first available provider that supports the requested model. If a provider fails, it tries the next available provider with that model.

Example: If both OpenAI and OpenRouter support `kimi-k2`, the request will first try OpenRouter (free), then fall back to Groq than OpenRouter (Paid) if requests fails.

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `LLMS_CONFIG_PATH`        | Custom config file path | `/path/to/llms.json` |
| `OPENAI_API_KEY`          | OpenAI API key | `sk-...` |
| `ANTHROPIC_API_KEY`       | Anthropic API key | `sk-ant-...` |
| `GOOGLE_API_KEY`          | Google API key | `AIza...` |
| `GROQ_API_KEY`            | Groq API key | `gsk_...` |
| `MISTRAL_API_KEY`         | Mistral API key | `...` |
| `OPENROUTER_API_KEY`      | OpenRouter API key | `sk-or-...` |
| `OPENROUTER_FREE_API_KEY` | OpenRouter free tier key | `sk-or-...` |
| `CODESTRAL_API_KEY`       | Codestral API key | `...` |
| `GROK_API_KEY`            | Grok (X.AI) API key | `xai-...` |
| `DASHSCOPE_API_KEY`       | Qwen (Alibaba Cloud) API key | `sk-...` |

## Configuration Examples

### Minimal Configuration

```json
{
  "defaults": {
    "headers": {"Content-Type": "application/json"},
    "text": {
      "model": "kimi-k2",
      "messages": [{"role": "user", "content": ""}]
    }
  },
  "providers": {
    "groq": {
      "enabled": true,
      "type": "OpenAiProvider",
      "base_url": "https://api.groq.com/openai",
      "api_key": "$GROQ_API_KEY",
      "models": {
        "llama3.3:70b": "llama-3.3-70b-versatile",
        "llama4:109b": "meta-llama/llama-4-scout-17b-16e-instruct",
        "llama4:400b": "meta-llama/llama-4-maverick-17b-128e-instruct",
        "kimi-k2": "moonshotai/kimi-k2-instruct-0905",
        "gpt-oss:120b": "openai/gpt-oss-120b",
        "gpt-oss:20b": "openai/gpt-oss-20b",
        "qwen3:32b": "qwen/qwen3-32b"
      }
    }
  }
}
```

### Multi-Provider Setup

```json
{
  "providers": {
    "openrouter": {
      "enabled": false,
      "type": "OpenAiProvider",
      "base_url": "https://openrouter.ai/api",
      "api_key": "$OPENROUTER_API_KEY",
      "models": {
        "grok-4": "x-ai/grok-4",
        "glm-4.5-air": "z-ai/glm-4.5-air",
        "kimi-k2": "moonshotai/kimi-k2",
        "deepseek-v3.1:671b": "deepseek/deepseek-chat",
        "llama4:400b": "meta-llama/llama-4-maverick"
      }
    },
    "anthropic": {
      "enabled": false,
      "type": "OpenAiProvider",
      "base_url": "https://api.anthropic.com",
      "api_key": "$ANTHROPIC_API_KEY",
      "models": {
        "claude-sonnet-4-0": "claude-sonnet-4-0"
      }
    },
    "ollama": {
      "enabled": false,
      "type": "OllamaProvider",
      "base_url": "http://localhost:11434",
      "models": {},
      "all_models": true
    }
  }
}
```

## Usage

    Run `llms` without arguments to see the help screen:

    usage: llms.py [-h] [--config FILE] [-m MODEL] [--chat REQUEST] [-s PROMPT] [--image IMAGE] [--audio AUDIO]
                  [--file FILE] [--raw] [--list] [--serve PORT] [--enable PROVIDER] [--disable PROVIDER]
                  [--default MODEL] [--init] [--logprefix PREFIX] [--verbose] [--update]

    llms

    options:
      -h, --help            show this help message and exit
      --config FILE         Path to config file
      -m MODEL, --model MODEL
                            Model to use
      --chat REQUEST        OpenAI Chat Completion Request to send
      -s PROMPT, --system PROMPT
                            System prompt to use for chat completion
      --image IMAGE         Image input to use in chat completion
      --audio AUDIO         Audio input to use in chat completion
      --file FILE           File input to use in chat completion
      --raw                 Return raw AI JSON response
      --list                Show list of enabled providers and their models (alias ls provider?)
      --serve PORT          Port to start an OpenAI Chat compatible server on
      --enable PROVIDER     Enable a provider
      --disable PROVIDER    Disable a provider
      --default MODEL       Configure the default model to use
      --init                Create a default llms.json
      --logprefix PREFIX    Prefix used in log messages
      --verbose             Verbose output
      --update              Update to latest version

## Troubleshooting

### Common Issues

**Config file not found**
```bash
# Initialize default config
llms --init

# Or specify custom path
llms --config ./my-config.json
```

**No providers enabled**

```bash
# Check status
llms --list

# Enable providers
llms --enable google anthropic
```

**API key issues**
```bash
# Check environment variables
echo $ANTHROPIC_API_KEY

# Enable verbose logging
llms --verbose "test"
```

**Model not found**

```bash
# List available models
llms --list

# Check provider configuration
llms ls openrouter
```

### Debug Mode

Enable verbose logging to see detailed request/response information:

```bash
llms --verbose --logprefix "[DEBUG] " "Hello"
```

This shows:
- Enabled providers
- Model routing decisions
- HTTP request details
- Error messages with stack traces

## Development

### Project Structure

- `llms.py` - Main script with CLI and server functionality
- `llms.json` - Default configuration file
- `requirements.txt` - Python dependencies

### Provider Classes

- `OpenAiProvider` - Generic OpenAI-compatible provider
- `OllamaProvider` - Ollama-specific provider with model auto-discovery
- `GoogleProvider` - Google Gemini with native API format
- `GoogleOpenAiProvider` - Google Gemini via OpenAI-compatible endpoint

### Adding New Providers

1. Create a provider class inheriting from `OpenAiProvider`
2. Implement provider-specific authentication and formatting
3. Add provider configuration to `llms.json`
4. Update initialization logic in `init_llms()`

## Contributing

Contributions are welcome! Please submit a PR to add support for any missing OpenAI-compatible providers.
