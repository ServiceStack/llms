# llms.py v3 Release Notes

**A major release focused on extensibility, expanded provider support, and enhanced user experience.**

---

## 🚀 What's New at a Glance

| Feature | Description |
|---------|-------------|
| **530+ Models** | Access over 530 models from 23 providers via [models.dev](https://models.dev) integration |
| **Extensions System** | Add features, providers, and customize the UI with a flexible plugin architecture |
| **Tool Support** | First-class Python function calling for LLM interactions with your local environment |
| **New Model Selector** | Redesigned full-featured dialog with search, filtering, sorting, and favorites |
| **Image Generation** | Built-in support for Google, OpenAI, OpenRouter, Chutes, and Nvidia |
| **Audio Generation** | TTS support for Gemini 2.5 Flash/Pro Preview models |
| **Improved Caching** | Persistent image/file caching with optimized storage |

---

## Table of Contents

- [Rewritten for Extensibility](#rewritten-for-extensibility)
- [New Provider Configuration Model](#new-provider-configuration-model)
- [New Model Selector UI](#new-model-selector-ui)
- [Extensions System](#extensions-system)
- [Tool Support](#tool-support)
- [Available Tools](#available-tools)
- [Image Generation Support](#image-generation-support)
- [Audio Generation Support](#audio-generation-support)
- [Image Cache & Optimization](#image-cache--optimization)
- [Enhancements & Fixes](#enhancements--fixes)
- [v3 Configuration Migration](#v3-configuration-migration)

---

## Rewritten for Extensibility

A major rewrite of llms has been completed to make it extensible and allow for easy addition of new features and providers. Most built-in UI components have been [refactored into modules](https://github.com/ServiceStack/llms/tree/main/llms/ui/modules) utilizing the same extensibility APIs that any extension can use. In addition to adding new pages and features, extensions can also **replace existing components** by registering new components with the same name.

Likewise, the server has adopted a **server extension model**, where major provider implementations can now be dropped into the [llms/providers](https://github.com/ServiceStack/llms/tree/main/llms/providers) folder, where they will be automatically loaded and registered at runtime.

This approach allows [main.py](https://github.com/ServiceStack/llms/blob/main/llms/main.py) to retain a **lean functional core in a single file** whilst still being fully extensible.

---

## New Provider Configuration Model

The most significant change is the migration to utilize the same [models.dev](https://models.dev) open provider and model catalogue as used and maintained by [OpenCode](https://opencode.ai).

llms provider configuration is now a **superset** of `models.dev/api.json` where its definitions are merged, allowing you to enable providers using just `"enabled": true` where it inherits all standard provider configurations from models.dev.

### 🌐 Expanded Provider Support

The switch to models.dev greatly expands the model selection to over **530 models** from **23 different providers**, including new support for:

| Provider | Models | Provider | Models |
|----------|--------|----------|--------|
| Alibaba | 39 | Hugging Face | 14 |
| Chutes | 56 | LMStudio | local |
| DeepSeek | 2 | MiniMax | 1 |
| Fireworks AI | 12 | Moonshot AI | 5 |
| GitHub Copilot | 27 | Nvidia | 24 |
| GitHub Models | 55 | Zai | 6 |
| | | Zai Coding Plan | 6 |

> 💡 Please [raise an issue](https://github.com/ServiceStack/llms/issues) to add support for any missing providers from [models.dev](https://models.dev) you would like to use.

### 🔄 Automatic Provider Updates

llms automatically updates your `providers.json` with the latest provider list from models.dev **daily**. You can also trigger a manual update anytime:

```bash
llms --update-providers
```

llms filters and saves only the providers that are referenced in your `llms.json`. Any additional providers you want to use that are not included in models.dev can be added to your `~/.llms/providers-extra.json`, which will be merged into your `providers.json` when updated.

This optimization keeps your local configuration file lightweight by only containing the providers that are available for use.

### Configuration Examples

#### 1. Simple Enable
Enable providers by ID — all configuration is automatically inherited:

```json
{
  "openai": { "enabled": true },
  "xai": { "enabled": true }
}
```

#### 2. Custom Configuration
Overlay custom settings like `temperature`, custom check requests, and provider-specific configuration:

```json
{
  "github-models": {
    "enabled": false,
    "check": {
      "messages": [{ "role": "user", "content": [{ "type": "text", "text": "1+1=" }] }],
      "stream": false
    }
  },
  "minimax": {
    "enabled": true,
    "temperature": 1.0
  },
  "google": {
    "enabled": true,
    "map_models": {
      "gemini-flash-latest": "gemini-flash-latest",
      "gemini-flash-lite-latest": "gemini-flash-lite-latest",
      "gemini-2.5-pro": "gemini-2.5-pro",
      "gemini-2.5-flash": "gemini-2.5-flash",
      "gemini-2.5-flash-lite": "gemini-2.5-flash-lite"
    },
    "safety_settings": [
      {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_ONLY_HIGH"
      }
    ],
    "thinking_config": {
      "thinkingBudget": 1024,
      "includeThoughts": true
    }
  }
}
```

#### 3. Model Mapping (`map_models`)
Use `map_models` to explicitly whitelist and map specific models. Only the models listed here will be enabled for the provider, using definitions from models.dev:

```json
{
  "alibaba": {
    "enabled": true,
    "map_models": {
      "qwen3-max": "qwen3-max",
      "qwen-max": "qwen-max",
      "qwen-plus": "qwen-plus"
    },
    "enable_thinking": false
  }
}
```

#### 4. Custom Provider & Model Definitions
Fully define custom OpenAI-compatible providers and models that aren't in models.dev. For example, adding Mistral's free `codestral` endpoint:

```json
{
  "codestral": {
    "enabled": true,
    "id": "codestral",
    "npm": "codestral",
    "api": "https://codestral.mistral.ai/v1",
    "env": ["CODESTRAL_API_KEY"],
    "models": {
      "codestral-latest": {
        "id": "codestral-latest",
        "name": "Codestral",
        "cost": { "input": 0.0, "output": 0.0 }
      }
    }
  }
}
```

> 💡 **Tip:** To enable access to new models for existing providers while waiting for them to be added to models.dev, add them to your `~/.llms/providers-extra.json`, where they'll be merged into your `providers.json` when updated.

#### 5. NPM SDK Alignment
The provider configuration is closely aligned with the models.dev npm configuration. The `"npm"` field maps the provider configuration to the correct Python provider implementation. This generic mapping allows for flexible provider support, including **Anthropic Chat Completion** requests, which are correctly handled for both **Anthropic** and **MiniMax** providers.

#### 6. Ecosystem Compatibility
By standardizing on models.dev definitions, the project now shares a compatible configuration model with other AI tools like **OpenCode**. This includes the standardization of environment variables using the `"env"` property, ensuring simpler and more portable configuration across different tools.

---

## New Model Selector UI

With over 530 models from 23 providers now available, discovering and selecting the right model required a complete overhaul. The Model Selector has been completely redesigned as a full-featured dialog offering:

### 🔍 Smart Search & Discovery
- **Full-text Search**: Instantly search across model names, IDs, and providers
- **Real-time Filtering**: Results update as you type with no lag
- **Model Count Display**: See how many models match your current filters

### 🎯 Advanced Filtering
- **Provider Filtering**: Click any provider to show only their models, with model counts displayed for each
- **Modality Filtering**: Filter by input/output capabilities (text, image, audio, video, PDF)
- **Favorites Tab**: Quick access to your most-used models with a dedicated favorites view
- **Unavailable Favorites**: Gracefully handles favorited models whose providers are disabled

### 📊 Flexible Sorting
Sort models by multiple criteria with ascending/descending toggle:
- **Knowledge Cutoff**: Find models with the most recent training data
- **Release Date**: Discover the newest models
- **Last Updated**: See which models are actively maintained
- **Cost**: Sort by input or output token pricing
- **Context Window**: Find models with the largest context limits
- **Name**: Classic alphabetical sorting

### ⭐ Favorites System
- **One-Click Favoriting**: Star icon on each model card
- **Persistent Storage**: Favorites saved to localStorage
- **Smart Defaults**: Favorites tab shown by default when you have favorites
- **Easy Management**: Remove favorites from any view

### 💎 Rich Model Cards
Each model displays comprehensive information at a glance:
- **Provider Icon**: Visual identification of the model's provider
- **Cost Information**: Input/output pricing per 1M tokens with "FREE" badge for free models
- **Context Limits**: Maximum context window and output token limits
- **Knowledge Cutoff**: Training data recency
- **Capabilities**: Visual badges for reasoning and tool calling support
- **Modality Icons**: Input/output support for image, audio, video, and PDF

---

## Extensions System

To keep the core lightweight while enabling limitless enhancements, we've introduced a flexible **Extensions system**. This allows you to add features, register new provider implementations, extend, replace, and customize the UI with your own custom features.

### Installation
Extensions can be installed from GitHub or by creating a local folder:
- **GitHub**: Clone extensions into `~/.llms/extensions` (e.g., `git clone https://github.com/user/repo ~/.llms/extensions/my_extension`)
- **Local**: Simply create a folder in `~/.llms/extensions/my_extension`

### How it Works (Server)
Extensions are Python modules that plug into the server lifecycle using special hooks defined in their `__init__.py`:

| Hook | Purpose |
|------|---------|
| `__parser__(parser)` | Add custom CLI arguments |
| `__install__(ctx)` | Enhance the server instance (routes, providers, filters, etc.) |
| `__run__(ctx)` | Execute custom logic when running in CLI mode |

The `ctx` parameter provides access to the `ExtensionContext`.

### How it Works (UI)
Extensions can also include frontend components:

1. **Placement**: Add a `ui` folder within your extension directory
2. **Access**: Files in this folder are automatically served at `/ext/<extension_name>/*`
3. **Integration**: Create a `ui/index.mjs` file. This is the entry point and must export an `install` function:

```javascript
const MyComponent = {
    template: `...`
}

// ui/index.mjs
export default {
    install(ctx) {
        // Register or replace components, add routes, etc.
        ctx.components({ MyComponent })
    }
}
```

### Example: [`system_prompts`](https://github.com/llmspy/system_prompts)
The `system_prompts` extension demonstrates these capabilities by allowing users to manage custom system prompts. It uses `__install__` to register an API endpoint and a UI extension to provide a management interface.

### Managing Extensions

**List available extensions:**
```bash
llms --add
```

**Install an extension:**
```bash
llms --add system_prompts
```

**Install a 3rd-party extension:**
```bash
llms --add my_user/my_extension
```
> Clones the GitHub repo into `~/.llms/extensions/my_extension` and installs any `requirements.txt` dependencies.

**List installed extensions:**
```bash
llms --remove
```

**Remove an extension:**
```bash
llms --remove system_prompts
```

---

## Tool Support

New in v3 is **first-class support for Python function calling (Tools)**, allowing LLMs to interact with your local environment and custom logic.

### 1. Python Function Tools
Define tools using standard Python functions. The system automatically generates tool definitions from your function's signature, type hints, and docstrings:

```python
def get_current_time(timezone: str = "UTC") -> str:
    """Get current time in the specified timezone"""
    return f"The time is {datetime.now().strftime('%I:%M %p')} {timezone}"
```

### 2. Registration
Register your tools within an extension's `install` method. You can register simple functions or provide manual definitions for complex cases:

```python
def install(ctx):
    # Automatic definition from function signature
    ctx.register_tool(get_current_time)
```

### 3. UI Management
- **One-Click Enable/Disable**: Use the new Tool Selector in the chat interface (top-right) to control which tools are available to the model
- **Dedicated Tools Page**: View all registered tools and their definitions at `/tools` or via the sidebar link
- **Granular Control**: Select "All", "None", or specific tools for each chat session

---

## Available Tools

All available tools are maintained in the GitHub [llmspy organization](https://github.com/orgs/llmspy/repositories):

| Tool | Description |
|------|-------------|
| `core_tools` | Core System Tools providing essential file operations, memory persistence, math expression evaluation, and code execution |
| `duckduckgo` | Add web search capabilities using DuckDuckGo |
| `system_prompts` | Enables and includes a collection of awesome system prompts |

**Install a tool:**
```bash
llms --add core_tools
```

Installing an extension clones it into your `~/.llms/extensions` folder and installs any Python `requirements.txt` dependencies. You can remove an extension by deleting the folder from `~/.llms/extensions`.

**Install 3rd-party extensions:**
```bash
llms --add <user>/<repo>
```

**Manual installation:**
```bash
git clone https://github.com/<user>/<repo> ~/.llms/extensions/<repo>
```

> 🤝 Feel free to submit pull requests to add new extensions to the [llmspy organization](https://github.com/orgs/llmspy/repositories) to make your extension easily discoverable to everyone.

---

## Image Generation Support

v3 includes built-in support for image generation models on:

| Provider | Status |
|----------|--------|
| Google | ✅ Supported |
| OpenAI | ✅ Supported |
| OpenRouter | ✅ Supported |
| Chutes | ✅ Supported |
| Nvidia | ✅ Supported |

> ⚠️ Since there is no standard way to generate images, this required a custom implementation for each provider.

### Command-Line Usage

Generate images using the `--out image` modifier:

```bash
llms --out image "cat in a hat"
```

This uses the `out:image` chat template in `llms.json` by default.

### Specify a Model

Use any model that supports image generation by specifying its **ID** or **name**:

```bash
llms -m "gemini-2.5-flash-image" --out image "cat in a hat"
llms -m "Gemini 2.5 Flash Image" --out image "cat in a hat"
```

> 📁 All generated images are saved to the `~/.llms/cache` folder using their SHA-256 hash as the filename.

---

## Audio Generation Support

v3 includes built-in support for audio generation with Google's new Text-to-Speech models:

| Model | Description |
|-------|-------------|
| **Gemini 2.5 Flash Preview TTS** | Fast, lightweight TTS |
| **Gemini 2.5 Pro Preview TTS** | High-quality TTS |

### UI & Command-Line Usage

Available in both the UI and on the command-line using `--out audio`:

```bash
llms --out audio "Merry Christmas"
llms -m gemini-2.5-pro-preview-tts --out audio "Merry Christmas"
```

### Output

Audio files are saved locally and accessible via HTTP URL:

```
Saved files:
/Users/llmspy/.llms/cache/c2/c27b5fd43ebbdbca...acf118.wav
http://localhost:8000/~cache/c2/c27b5fd43ebbdbca...acf118.wav
```

### Playback

**From the command line:**
```bash
play /Users/llmspy/.llms/cache/c2/c27b5fd43ebbdbca...acf118.wav
```

**From the browser:**
Run the server with `llms --serve 8000` and access the URL to play in your browser.

---

## Image Cache & Optimization

A new caching system has been implemented for uploaded images and files. Uploads are now persisted in `~/.llms/cache`, preserving them across messages and sessions.

- **Efficient Storage**: Only cache references are stored in the browser history and sent with chat messages, significantly reducing local storage usage and payload size
- **Persistent Access**: Images remain accessible for previews and downloads even after page reloads
- **Automatic Management**: The system handles file storage and serving transparently, ensuring a smooth user experience

---

## Enhancements & Fixes

### Improved Model Selection
Models can now be selected via CLI or API using flexible resolution logic:
- **Case-Insensitive**: Match models regardless of casing
- **Short Form**: Use short names (e.g., `gpt-4o` instead of `openai/gpt-4o`)
- **Display Names**: Select models by their human-readable **name** (from models.dev)

### Cost Display
Updated cost metrics to consistently display price per **1 Million tokens**.

### Chat History Improvements
Fixed context preservation for Image URLs in chat history, ensuring follow-up requests retain full context.

### Provider Updates

| Provider | Update |
|----------|--------|
| **Anthropic** | Native implementation of the chat method |
| **Ollama** | Fixed endpoint handling (resolved 404 errors) |
| **LMStudio** | New support with dynamic model selection |

---

## v3 Configuration Migration

> ⚠️ **Breaking Change**: The v3 `llms.json` configuration is **not compatible** with v2 as it has been completely overhauled to be a superset of models.dev.

When running llms v3 for the first time, it will:
1. **Automatically backup** your previous `llms.json`
2. **Create a new** `llms.json` with the v3 format

### Manual Cleanup Required

You should also delete your `LlmsThreads` IndexedDB as it's not compatible with v3:

1. Open browser DevTools (F12)
2. Go to **Application** → **IndexedDB**
3. Delete the `LlmsThreads` database

---

## Upgrade Instructions

```bash
# Update llms to v3
pip install --upgrade llms

# Update provider definitions
llms --update-providers

# Start the server
llms --serve 8000
```

**Happy holidays from the llms.py team!** 🎄

