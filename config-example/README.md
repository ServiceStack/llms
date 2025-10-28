# Custom Configuration Example

This directory shows how to use custom `llms.json` and `ui.json` configuration files with the Docker container.

## Quick Start

### Option 1: Extract Default Configs

Use the provided script to extract the default configuration files:

```bash
# Extract to ./config directory
../docker-extract-configs.sh config

# Or specify a custom directory
../docker-extract-configs.sh my-custom-config
```

### Option 2: Manual Extraction

```bash
# Create config directory
mkdir -p config

# Run container with init to create default configs
docker run --rm -v $(pwd)/config:/home/llms/.llms \
  ghcr.io/servicestack/llms:latest \
  llms --init
```

## Using Custom Configs

### With docker-compose

1. Place your `llms.json` and `ui.json` in a directory (e.g., `./config`)

2. Update `docker-compose.yml`:

```yaml
volumes:
  - ./config:/home/llms/.llms
```

3. Start the service:

```bash
docker-compose up -d
```

### With docker run

```bash
docker run -p 8000:8000 \
  -v $(pwd)/config:/home/llms/.llms \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest
```

## Configuration Examples

### Minimal llms.json (Groq only)

```json
{
  "defaults": {
    "headers": {
      "Content-Type": "application/json"
    },
    "text": {
      "model": "llama3.3:70b",
      "messages": [
        {
          "role": "user",
          "content": ""
        }
      ]
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
        "llama4:400b": "meta-llama/llama-4-maverick-17b-128e-instruct"
      }
    }
  }
}
```

### Custom Provider Configuration

Add a custom OpenAI-compatible provider:

```json
{
  "providers": {
    "my-custom-provider": {
      "enabled": true,
      "type": "OpenAiProvider",
      "base_url": "https://api.example.com/v1",
      "api_key": "$MY_CUSTOM_API_KEY",
      "models": {
        "my-model": "provider-model-name"
      }
    }
  }
}
```

### Enable Only Free Providers

```json
{
  "providers": {
    "openrouter_free": {
      "enabled": true,
      "type": "OpenAiProvider",
      "base_url": "https://openrouter.ai/api",
      "api_key": "$OPENROUTER_API_KEY",
      "models": {
        "llama4:400b": "meta-llama/llama-4-maverick:free",
        "deepseek-v3.1:671b": "deepseek/deepseek-chat-v3.1:free"
      }
    },
    "groq": {
      "enabled": true,
      "type": "OpenAiProvider",
      "base_url": "https://api.groq.com/openai",
      "api_key": "$GROQ_API_KEY",
      "models": {
        "llama3.3:70b": "llama-3.3-70b-versatile"
      }
    },
    "google_free": {
      "enabled": true,
      "type": "GoogleProvider",
      "base_url": "https://generativelanguage.googleapis.com",
      "api_key": "$GOOGLE_FREE_API_KEY",
      "models": {
        "gemini-2.0-flash": "gemini-2.0-flash-exp"
      }
    }
  }
}
```

## What Can You Customize?

### In llms.json

- **Enable/disable providers**: Set `"enabled": true/false` for each provider
- **Add/remove models**: Modify the `"models"` object for each provider
- **API endpoints**: Change `"base_url"` to use different endpoints
- **API keys**: Use environment variables like `"$MY_API_KEY"` or hardcode (not recommended)
- **Default model**: Set `"defaults.text.model"` to your preferred model
- **Chat templates**: Customize `"defaults.text"`, `"defaults.image"`, `"defaults.audio"`, etc.
- **Pricing**: Add custom pricing information for cost tracking
- **Provider-specific settings**: Configure timeouts, retries, etc.

### In ui.json

- **UI theme**: Customize colors, fonts, layout
- **Default selections**: Set default model, provider preferences
- **Feature toggles**: Enable/disable UI features
- **Custom branding**: Add your own branding elements

## Tips

1. **Use environment variables for API keys**: Instead of hardcoding keys, use `"$ENV_VAR_NAME"` format
2. **Start with defaults**: Extract the default configs and modify them incrementally
3. **Test changes**: After modifying configs, restart the container to apply changes
4. **Backup your configs**: Keep your custom configs in version control (without API keys)
5. **Read-only mounts**: Use `:ro` suffix when mounting to prevent accidental modifications

## Troubleshooting

### Config not loading

- Ensure files are named exactly `llms.json` and `ui.json`
- Check file permissions (should be readable by UID 1000)
- Verify JSON syntax is valid (use a JSON validator)

### Changes not applying

- Restart the container after modifying configs
- Check container logs: `docker logs llms-server`

### Permission errors

```bash
# Fix permissions
chmod 644 config/llms.json config/ui.json
```

## More Information

- See [DOCKER.md](../DOCKER.md) for detailed Docker usage
- See [README.md](../README.md) for general llms-py documentation
- See [llms/llms.json](../llms/llms.json) for the full default configuration

