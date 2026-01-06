---
title: v3 Release Notes
description: Major release focused on extensibility, expanded provider support, and enhanced user experience.
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
| **Media Gallery** | Beautiful UI to browse generated portrait/landscape images and audio generations |
| **Run Code UI** | Execute Python, JavaScript, TypeScript and C# code scripts in a CodeMirror editor |
| **Calculator UI** | Beautiful UX Friendly UI to evaluate python math expressions |
| **KaTeX Support** | Support for beautiful rendering of LaTeX math expressions |
| **SQLite Storage** | Migrated from IndexedDB to server SQLite for robust persistence and concurrent usage |
| **Asset Caching** | Persistent image/file file caching with metadata |

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

llms.py has been rewritten from the ground-up to make extensibility a **core concept** with all [major UI and Server features](https://github.com/ServiceStack/llms/tree/main/llms/extensions) now layering their encapsulated functionality using the public Extensibility APIs.

Extensions are just folders that can add both Server and UI features using the public client and server extensibility APIs. Built-in features are just extensions in the repo's [llms/extensions](https://github.com/ServiceStack/llms/tree/main/llms/extensions) folder which can be overridden by adding them to your local `~/.llms/extensions` folder.

llms includes support for installing and uninstalling extensions from any GitHub repository. For better discoverability, non built-in extensions will be maintained in the [llmspy](https://github.com/orgs/llmspy/repositories) GitHub organization repositories which anyone else is welcome to contribute their repos to, to improve their discoverability.

UI components are now registered and referenced as Global Vue components, which can be easily replaced by registering new Vue components with the same name as demonstrated in the [xmas](https://github.com/llmspy/xmas/blob/main/ui/index.mjs) extension demo.

This approach allows [main.py](https://github.com/ServiceStack/llms/blob/main/llms/main.py) to retain a **lean functional core in a single file** whilst still being fully extensible and lays the foundation for **rapid development of new optional features** - both from the core team and external 3rd party extensions - enabling the community to extend llms.py in new unanticipated ways.

---

## New Provider Configuration Model

The most significant change is the migration to utilize the same [models.dev](https://models.dev) open provider and model catalogue as used and maintained by [OpenCode](https://opencode.ai).

**llms.json** provider configuration is now a **superset** of `models.dev/api.json` where its definitions are merged, allowing you to enable providers using just `"enabled": true` to inherit provider configurations from **models.dev**

### 🌐 Expanded Provider Support

The switch to [models.dev](https://models.dev) greatly expands the model selection to over **530 models** from **23 different providers**, including new support for:

| Provider       | Models   | Provider        | Models   |
|----------------|----------|-----------------|----------|
| Alibaba        | 39       | Hugging Face    | 14       |
| Chutes         | 56       | Zai Coding Plan | 6        |
| DeepSeek       | 2        | MiniMax         | 1        |
| Fireworks AI   | 12       | Moonshot AI     | 5        |
| GitHub Copilot | 27       | Nvidia          | 24       |
| GitHub Models  | 55       | Zai             | 6        |
| LMStudio       | local    | Ollama          | local    |

Non OpenAI Compatible LLM and Image generation providers are maintained in the [providers](https://github.com/ServiceStack/llms/tree/main/llms/extensions/providers) extension, registered using the `ctx.add_provider()` API.

<Tip>💡 [Raise an issue](https://github.com/ServiceStack/llms/issues) to add support for any missing providers from [models.dev](https://models.dev) you would like to use.</Tip>

### 🔄 Automatic Provider Updates

This actively maintained list of available providers and models are automatically updated into your `providers.json` daily that can also be manually updated with:

```bash
llms --update-providers
```

As an optimization only the providers that are referenced in your `llms.json` are saved. Any additional providers you want to use that are not included in models.dev can be added to your `~/.llms/providers-extra.json`, which get merged into your `providers.json` on every update.

This keeps your local configuration file lightweight by only including the providers that are available for use.

### Configuration Examples

Enable providers by ID — all configuration is automatically inherited:

```json
{
  "openai": { "enabled": true },
  "xai": { "enabled": true }
}
```

See [Configuration](/docs/configuration) docs for more info.

---

## New Model Selector UI

With over 530 models from 23 providers now available, discovering and selecting the right model required a complete overhaul. 
The Model Selector has been completely redesigned as a full-featured dialog offering:

- **🔍 Smart Search & Discovery** - Instantly search across model names, IDs, and providers
- **🎯 Advanced Filtering** - Filter by name, providers & input and output modalities
- **📊 Flexible Sorting** - Sort by Knowledge Cutoff, Release Date, Last Updated & Context
- **⭐ Favorites System** - Star model card to add/remove to favorites quick list
- **💎 Rich Model Cards** - In depth model overview at a glance

[![](/img/v3/model-selector.webp)](/docs/features/model-selector)

---

## Extensions System

To keep the core lightweight while enabling limitless enhancements, we've implemented a flexible **Extensions system** inspired by ComfyUI Custom Nodes. This allows adding new features, pages and toolbar icons, register new provider implementations, extend, replace, and customize the UI with your own custom features.

### Installation
Extensions can be installed from GitHub or by creating a local folder:
- **Local**: Simply create a folder in `~/.llms/extensions/my_extension`
- **GitHub**: Clone extensions into `~/.llms/extensions`, e.g:

```
git clone https://github.com/user/repo ~/.llms/extensions/my_extension
```

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

### Example: [`xmas`](https://github.com/llmspy/xmas) extension
The `xmas` extension demonstrates these capabilities where it utilizes the Extensions APIs to give llms.py a splash of Christmas spirit. It uses `__install__` to register an API endpoint and a UI extension for its UI features.

### Server API

**[__init__.py](https://github.com/llmspy/xmas/blob/main/__init__.py)**

The xmas extension uses the `__install__` hook which runs after providers are configured to add new Server functionality by registering new aiohttp web application handlers.

For example this registers both **GET** and **POST** handlers to a new `/ext/xmas/greet` endpoint to return new Xmas greeting:

```python
def install(ctx):
    async def greet(request):
        nonlocal count
        name = request.query.get('name')
        if not name:
            data = await request.post()
            name = data.get('name')

        if not name:
            name = 'Stranger'

        greeting = greetings[count % len(greetings)]
        count += 1
        return web.json_response({"result":f"Hello {name}, {greeting}"})

    ctx.add_get("greet", greet)
    ctx.add_post("greet", greet)


# register install extension handler
__install__ = install
```

## UI Extensions

[/ui/index.mjs](https://github.com/llmspy/xmas/blob/main/ui/index.mjs)



### Managing Extensions

**List available extensions:**
```bash
llms --add
```

Output:

```
Available extensions:
  core_tools       Essential tools for memory, file operations, math expressions and code execution
  system_prompts   Enables and includes collection of awesome system prompts
  duckduckgo       Add web search tool capabilities using Duck Duck Go
  xmas             Example of utilizing the Extensions APIs to give llms.py some Christmas spirit

Usage:
  llms --add <extension>
llms --add <github-user>/<repo>
```

**Install an extension:**
```bash
llms --add core_tools
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
| `xmas` | Example of utilizing the Extensions APIs to give llms.py some Christmas spirit |

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


---

<ScreenshotsGallery className="mb-8" gridClass="grid grid-cols-1 md:grid-cols-2 gap-4" images={{
    'New Model Selector': '/img/v3/model-selector.webp',
}} />
