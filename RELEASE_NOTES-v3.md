# v3 Release Notes

## Rewritten for extensibility

A major rewrite of llms has been completed to make it extensible and allow for easy addition of new features and providers. Including built-in UI components which have been [refactored into modules](https://github.com/ServiceStack/llms/tree/main/llms/ui/modules) utilizing the same extensibility APIs any extension will be able to use. In addition to adding new features, extensions are also to replace existing components by registering new components with the same name.

Likewise, the server has adopted a server extension model, where major provider implementations can now be dropped into the [llms/providers](https://github.com/ServiceStack/llms/tree/main/llms/providers) folder where it will be automatically loaded and registered at runtime.

This allows [main.py](https://github.com/ServiceStack/llms/blob/main/llms/main.py) to continue to retain a lean functional core in a single file whislt still being extensible.

## New Provider Configuration Model

The most disruptive change is the migration to utilize the same [models.dev](https://models.dev) open provider and model catalogue as used and maintained by [OpenCode](https://opencode.ai).

llms provider configuration is now a **superset** of `models.dev/api.json` where its definitions are merged, allowing you to enable providers using just `"enabled": true` where it inherits all standard provider configurations from `models.dev`.

The switch to models.dev greatly expands our model selection to more than 530 models from 23 different providers. Including new support for:

- Alibaba (39 models)
- Chutes (56 models)
- DeepSeek (2 models)
- Fireworks AI (12 models)
- GitHub Copilot (27 models)
- GitHub Models (55 models)
- Hugging Face (14 models)
- LMStudio (http://127.0.0.1:1234)
- MiniMax (1 model)
- Moonshot AI (5 models)
- Nvidia (24 models)
- Zai (6 models)
- Zai Coding Plan (6 models)

Please raise an issue to add support for any missing providers from [models.dev](https://models.dev) you would like to use.

### Up to Date Providers

This also allows `llms` to automatically update your `providers.json` with the latest provider list from `models.dev` daily. You can also use the `--update-providers` command at anytime to update your local `providers.json` with the latest provider list from `models.dev`. 

`llms` filters and saves only the providers that are referenced in your `llms.json`. Any additional providers you want to use that are not included in `models.dev` can be added to your `~/.llms/providers-extra.json` which will be merged into your `providers.json` when updated.

This optimization keeps your local configuration file lightweight by only containing the providers that are available for use.

## New Model Selector UI

With over 530 models from 23 providers now available, discovering and selecting the right model required a complete overhaul from a simple Autocomplete. The Model Selector has been completely redesigned as a full-featured dialog offering:

### 🔍 **Smart Search & Discovery**
- **Full-text Search**: Instantly search across model names, IDs, and providers
- **Real-time Filtering**: Results update as you type with no lag
- **Model Count Display**: See how many models match your current filters

### 🎯 **Advanced Filtering**
- **Provider Filtering**: Click any provider to show only their models, with model counts displayed for each
- **Modality Filtering**: Filter by input/output capabilities (text, image, audio, video, PDF)
- **Favorites Tab**: Quick access to your most-used models with a dedicated favorites view
- **Unavailable Favorites**: Gracefully handles favorited models whose providers are disabled

### 📊 **Flexible Sorting**
Sort models by multiple criteria with ascending/descending toggle:
- **Knowledge Cutoff**: Find models with the most recent training data
- **Release Date**: Discover the newest models
- **Last Updated**: See which models are actively maintained
- **Cost**: Sort by input or output token pricing
- **Context Window**: Find models with the largest context limits
- **Name**: Classic alphabetical sorting

### ⭐ **Favorites System**
- **One-Click Favoriting**: Star icon on each model card
- **Persistent Storage**: Favorites saved to localStorage
- **Smart Defaults**: Favorites tab shown by default when you have favorites
- **Easy Management**: Remove favorites from any view

### 💎 **Rich Model Cards**
Each model displays comprehensive information at a glance:
- **Provider Icon**: Visual identification of the model's provider
- **Cost Information**: Input/output pricing per 1M tokens with "FREE" badge for free models
- **Context Limits**: Maximum context window and output token limits
- **Knowledge Cutoff**: Training data recency
- **Capabilities**: Visual badges for reasoning and tool calling support
- **Modality Icons**: Input/output support for image, audio, video, and PDF

### 1. Simple Enable
Enable providers by id — all configuration is automatically inherited:
```json
"openai": { "enabled": true },
"xai": { "enabled": true }
```

### 2. Custom Configuration
You can overlay custom settings like `temperature`, custom check requests and provider-specific configuration as used in Google's Gemini Provider:
```json
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
    },
}
```

### 3. Model Mapping (`map_models`)
Use `map_models` to explicitly whitelist and map specific models. Only the models listed here will be enabled for the provider, using definitions from `models.dev`:
```json
"alibaba": {
    "enabled": true,
    "map_models": {
        "qwen3-max": "qwen3-max",
        "qwen-max": "qwen-max",
        "qwen-plus": "qwen-plus",
        // ... other models
    },
    "enable_thinking": false
}
```

### 4. Custom Provider & Model Definitions

You can still fully define custom OpenAI compatible providers and models that aren't in `models.dev`. For example, adding Mistral's free `codestral` endpoint:

```json
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
            "cost": { "input": 0.0, "output": 0.0 },
            // ... full model definition
        }
    }
}
```

If you just want to enable access to new models for existing providers whilst you're waiting for them to be added to models.dev, you can add them to your ``~/.llms/providers-extra.json` where they'll be merged into your `providers.json` when updated.

### 5. NPM SDK Alignment
The provider configuration is now closely aligned with the `models.dev` npm configuration. The `"npm"` field is used to map the provider configuration to the correct Python provider implementation. This generic mapping allows for flexible provider support, including **Anthropic Chat Completion** requests, which are correctly handled for both **Anthropic** and **MiniMax** providers.

### 6. Ecosystem Compatibility
By standardizing on `models.dev` definitions, the project now shares a compatible configuration model with other AI tools like **OpenCode**. This includes the standardization of environment variables using the `"env"` property, ensuring simpler and more portable configuration across different tools.

## Extensions
To keep the core lightweight while enabling limitless enhancements, we've introduced a flexible Extensions system. This allows you to add features, register new provider implementations, extend, replace and customize the UI with your own custom features.

### Installation
Extensions can be installed from GitHub or by creating a local folder:
- **GitHub**: Clone extensions into `~/.llms/extensions` (e.g., `git clone https://github.com/user/repo ~/.llms/extensions/my_extension`).
- **Local**: Simply create a folder in `~/.llms/extensions/my_extension`.

### How it Works (Server)
Extensions are Python modules that plug into the server lifecycle using special hooks defined in their `__init__.py`:

- **`__parser__(parser)`**: Add custom CLI arguments.
- **`__install__(ctx)`**: Enhance the server instance (e.g., add routes, register providers, request/response filters, etc). `ctx` gives you access to the `ExtensionContext`.
- **`__run__(ctx)`**: Execute custom logic when running in CLI mode.

### How it Works (UI)
Extensions can also just include a frontend component.
1.  **Placement**: Add a `ui` folder within your extension directory.
2.  **Access**: Files in this folder are automatically served at `/ext/<extension_name>/*`.
3.  **Integration**: Create a `ui/index.mjs` file. This is the entry point and must export an `install` function:

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

### Installing Extensions

List available extensions:

```bash
llms --add
```

Install extension:

```bash
llms --add system_prompts
```

Install 3rd Party extension:

```bash
llms --add my_user/my_extension
```

> clones GitHub Repo into `~/.llms/extensions/my_extension` and installs any `requirements.txt` dependencies

### Removing Extensions

List installed extensions:

```bash
llms --remove
```

Remove extension:

```bash
llms --remove system_prompts
```

## Tool Support

New in v3 is first-class support for Python function calling (Tools), allowing LLMs to interact with your local environment and custom logic.

### 1. Python Function Tools

Define tools using standard Python functions. The system automatically generates tool definitions from your function's signature, type hints, and docstrings.

```python
def get_current_time(timezone: str = "UTC") -> str:
    """Get current time in the specified timezone"""
    return f"The time is {datetime.now().strftime('%I:%M %p')} {timezone}"
```

### 2. Registration
Register your tools within an extension's `install` method. You can register simple functions or provide manual definitions for complex cases.

```python
def install(ctx):
    # Automatic definition from function signature
    ctx.register_tool(get_current_time)
```

### 3. UI Management
- **One-Click Enable/Disable**: Use the new Tool Selector in the chat interface (top-right) to control which tools are available to the model.
- **Dedicated Tools Page**: View all registered tools and their definitions at `/tools` or via the sidebar link.
- **Granular Control**: Select "All", "None", or specific tools for each chat session.

## Available Tools

All available tools are maintained in GitHub [llmspy/repositories](https://github.com/orgs/llmspy/repositories), currently:

- `core_tools` - Core System Tools providing essential file operations, memory persistence, math expression evaluation, and code execution
- `duckduckgo` - Add web search tool capabilities using Duck Duck Go
- `system_prompts` - Enables and includes collection of awesome system prompts

```bash
llms --add core_tools
```

Installing an extension simply clones it into your `~/.llms/extensions` folder and installs any Python `requirements.txt` dependencies (if any). Inversely, you can remove an extension by deleting the folder from `~/.llms/extensions`.

You can also install 3rd Party extensions from GitHub using:

```bash
llms --add <user>/<repo>
```

Or by manually cloning it in your `~/.llms/extensions` folder:

```bash
git clone https://github.com/<user>/<repo> ~/.llms/extensions/<repo>
```

Feel free to submit pull requests to add new extensions to the [llmspy/repositories](https://github.com/orgs/llmspy/repositories) organization to make your extension easily discoverable to everyone.

## Image Generation Support

v3 includes built-in support for image generation models on:

- **Google**
- **OpenAI**
- **OpenRouter**
- **Chutes**
- **Nvidia**

As there is no standard way to generate images, this requires a custom implementation for each provider.

It can be generated from the command-line using the `--out image` modifier, e.g:

```bash
llms --out image "cat in a hat"
```

Which uses the `out:image` chat template by default.

It can be used with any model that supports image generation, e.g You can generate an image using Nano Banana by using its id or name:

```bash
llms -m "gemini-2.5-flash-image" --out image "cat in a hat"
llms -m "Gemini 2.5 Flash Image" --out image "cat in a hat"
```

All generated images are saved to the `~/.llms/cache` folder using their SHA-256 hash as the filename.

## Audio Generation Support

v3 includes built-in support for audio generation on Google's new TTS models:

- **Gemini 2.5 Flash Preview TTS**
- **Gemini 2.5 Pro Preview TTS**

This is available in both the UI and on the command-line using `--out audio`, e.g:

```bash
llms --out audio "Merry Christmas"
llms -m gemini-2.5-pro-preview-tts --out audio "Merry Christmas"  
```

Where it will save and generate both local file and HTTP URL links, e.g:

```
Saved files:
/Users/llmspy/.llms/cache/c2/c27b5fd43ebbdbca39459b510001eb8aaef622e5b947866ef78f36def9acf118.wav
http://localhost:8000/~cache/c2/c27b5fd43ebbdbca39459b510001eb8aaef622e5b947866ef78f36def9acf118.wav
```

Which you can either play from the command line:

```bash
play /Users/llmspy/.llms/cache/c2/c27b5fd43ebbdbca39459b510001eb8aaef622e5b947866ef78f36def9acf118.wav
```

Or run the Server using `llms --serve 8000` to play it in the browser.


## Image Cache & Optimization
A new caching system has been implemented for uploaded images and files. Uploads are now persisted in `~/.llms/cache`, preserving them across messages and sessions.
- **Efficient Storage**: Only cache references are stored in the browser history and sent with chat messages, significantly reducing local storage usage and payload size.
- **Persistent Access**: Images remain accessible for previews and downloads even after page reloads.
- **Automatic Management**: The system handles file storage and serving transparently, ensuring a smooth user experience.

## Enhancements & Fixes
- **Improved Model Selection**: Models can now be selected via CLI or API using a flexible resolution logic:
    - **Case-Insensitive**: Match models regardless of casing.
    - **Short Form**: Use short names (e.g., `gpt-4o` instead of `openai/gpt-4o`).
    - **Display Names**: Select models by their human-readable **name** (in models.dev)
- **Cost Display**: Updated cost metrics to consistently display price per **1 Million tokens**.
- **Chat History Improvements**: Fixed context preservation for Image URLs in chat history, ensuring follow-up requests retain full context.
- **Provider Updates**:
    - **Anthropic**: Native implementation of the chat method.
    - **Ollama**: Fixes for endpoint handling (resolving 404 errors).
    - **LMStudio**: New support for LM Studio, including dynamic model selection.

### v3 Configuration Migration

Unfortunately the new v3 llms.json configuration is not compatible with v2 as it has been completely overhauled to be a superset of models.dev. When running llms v3 it will automatically backup your previous llms.json and create a new llms.json with the new format.

You should also delete your `LlmsThreads` IndexedDB as it's also not compatible with v3.
