# v3 Release Notes

## New Provider Configuration Model

The internal provider configuration is now a **superset** of `models.dev/api.json`. Definitions are merged, allowing you to simply enable providers using `"enabled": true` while inheriting all standard provider configurations from `models.dev`.

The switch to models.dev greatly expands our model selection to not more than 530 providers from 23 different providers. Including new support for:

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
You can still fully define custom providers and models that aren't in `models.dev`. For example, adding Mistral's free `codestral` endpoint:
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

### 5. NPM SDK Alignment
The provider configuration is now closely aligned with the `models.dev` npm configuration. The `"npm"` field is used to map the provider configuration to the correct Python provider implementation. This generic mapping allows for flexible provider support, including **Anthropic Chat Completion** requests, which are correctly handled for both **Anthropic** and **MiniMax** providers.

### 6. Ecosystem Compatibility
By standardizing on `models.dev` definitions, the project now shares a compatible configuration model with other AI tools like **OpenCode**. This includes the standardization of environment variables using the `"env"` property, ensuring simpler and more portable configuration across different tools.

## Optimized `--update`
The `--update` command provides an optimal way to fetch the latest provider list from `models.dev` but saves only a subset to your local `providers.json`. It filters and saves only the providers that are referenced in your `llms.json`. This optimization keeps your local configuration file lightweight and focused on the providers you actually use.

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
