# Docker Support for llms-py

This document provides detailed information about running llms-py in Docker.

## Quick Start

### Using Pre-built Images

```bash
# Pull and run the latest image
docker pull ghcr.io/servicestack/llms:latest
docker run -p 8000:8000 -e OPENROUTER_API_KEY="your-key" ghcr.io/servicestack/llms:latest
```

### Using docker-compose (Recommended)

1. Create a `.env` file with your API keys:
```bash
OPENROUTER_API_KEY=sk-or-...
GROQ_API_KEY=gsk_...
GOOGLE_FREE_API_KEY=AIza...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROK_API_KEY=xai-...
DASHSCOPE_API_KEY=sk-...
ZAI_API_KEY=sk-...
MISTRAL_API_KEY=...
```

2. Start the service:
```bash
docker-compose up -d
```

3. Access the UI at http://localhost:8000

## Files Created

### Dockerfile
Multi-stage Docker build that:
- Uses Python 3.11 slim base image
- Builds the package from source
- Runs as non-root user for security
- Includes health checks
- Exposes port 8000
- Default command: `llms --serve 8000`

### .dockerignore
Excludes unnecessary files from the Docker build context to reduce image size and build time.

### docker-compose.yml
Provides easy orchestration with:
- Port mapping (8000:8000)
- Environment variable support via .env file
- Named volume for data persistence
- Automatic restart policy
- Health checks

### docker-build.sh
Convenience script for building the Docker image locally:
```bash
./docker-build.sh [tag]
```

### .github/workflows/docker-publish.yml
GitHub Actions workflow that:
- Builds Docker images on push to main and tags
- Publishes to GitHub Container Registry (ghcr.io)
- Supports multi-architecture builds (amd64, arm64)
- Creates image tags for branches, PRs, and semantic versions
- Uses Docker layer caching for faster builds

## Usage Examples

### Basic Server

```bash
docker run -p 8000:8000 \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest
```

### With Multiple API Keys

```bash
docker run -p 8000:8000 \
  -e OPENROUTER_API_KEY="sk-or-..." \
  -e GROQ_API_KEY="gsk_..." \
  -e GOOGLE_FREE_API_KEY="AIza..." \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  ghcr.io/servicestack/llms:latest
```

### With Persistent Storage

```bash
docker run -p 8000:8000 \
  -v llms-data:/home/llms/.llms \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest
```

### CLI Usage

```bash
# Single query
docker run --rm \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest \
  llms "What is the capital of France?"

# List models
docker run --rm \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest \
  llms --list

# Check provider
docker run --rm \
  -e GROQ_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest \
  llms --check groq
```

### Custom Port

```bash
# Run on port 3000
docker run -p 3000:8000 \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest
```

### With Verbose Logging

```bash
docker run -p 8000:8000 \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest \
  llms --serve 8000 --verbose
```

## Building Locally

### Using the Build Script

```bash
./docker-build.sh
```

This builds the image as `llms-py:latest`.

### Manual Build

```bash
docker build -t llms-py:latest .
```

### Build with Custom Tag

```bash
./docker-build.sh v2.0.24
```

## Docker Compose

### Start Services

```bash
docker-compose up -d
```

### View Logs

```bash
docker-compose logs -f
```

### Stop Services

```bash
docker-compose down
```

### Rebuild and Restart

```bash
docker-compose up -d --build
```

## Data Persistence

The container stores configuration and analytics data in `/home/llms/.llms`.

On first run, the container will automatically create default `llms.json` and `ui.json` files in this directory.

### Named Volume (Recommended)

```bash
docker run -p 8000:8000 \
  -v llms-data:/home/llms/.llms \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest
```

### Local Directory

```bash
docker run -p 8000:8000 \
  -v $(pwd)/llms-config:/home/llms/.llms \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest
```

## Custom Configuration Files

You can customize the behavior of llms-py by providing your own `llms.json` and `ui.json` configuration files.

### Method 1: Mount a Local Directory (Recommended)

1. Create a local directory with your custom config files:

```bash
# Option A: Use the provided extraction script (easiest)
./docker-extract-configs.sh config

# Option B: Manual extraction
mkdir -p config
docker run --rm -v $(pwd)/config:/home/llms/.llms \
  ghcr.io/servicestack/llms:latest \
  llms --init
```

2. Edit `config/llms.json` and `config/ui.json` to your preferences

3. Mount the directory when running the container:

```bash
docker run -p 8000:8000 \
  -v $(pwd)/config:/home/llms/.llms \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest
```

Or with docker-compose, update the volumes section:

```yaml
volumes:
  - ./config:/home/llms/.llms
```

### Method 2: Mount Individual Config Files

Mount specific config files (read-only recommended to prevent accidental changes):

```bash
docker run -p 8000:8000 \
  -v $(pwd)/my-llms.json:/home/llms/.llms/llms.json:ro \
  -v $(pwd)/my-ui.json:/home/llms/.llms/ui.json:ro \
  -e OPENROUTER_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest
```

Or with docker-compose:

```yaml
volumes:
  - ./my-llms.json:/home/llms/.llms/llms.json:ro
  - ./my-ui.json:/home/llms/.llms/ui.json:ro
```

### Method 3: Initialize and Extract Configs

1. Run the container with a named volume to initialize default configs:

```bash
docker run --rm \
  -v llms-data:/home/llms/.llms \
  ghcr.io/servicestack/llms:latest \
  llms --init
```

2. Extract the configs to customize them:

```bash
# Create a temporary container to copy files
docker run -d --name llms-temp -v llms-data:/home/llms/.llms ghcr.io/servicestack/llms:latest sleep 60
docker cp llms-temp:/home/llms/.llms/llms.json ./llms.json
docker cp llms-temp:/home/llms/.llms/ui.json ./ui.json
docker rm -f llms-temp
```

3. Edit the files and copy them back:

```bash
# After editing, copy back
docker run -d --name llms-temp -v llms-data:/home/llms/.llms ghcr.io/servicestack/llms:latest sleep 60
docker cp ./llms.json llms-temp:/home/llms/.llms/llms.json
docker cp ./ui.json llms-temp:/home/llms/.llms/ui.json
docker rm -f llms-temp
```

### What Can You Customize?

**In `llms.json`:**
- Enable/disable providers
- Add or remove models
- Configure API endpoints
- Set pricing information
- Customize default chat templates
- Configure provider-specific settings

**In `ui.json`:**
- UI theme and appearance
- Default model selections
- UI feature toggles
- Custom UI configurations

### Example: Custom Provider Configuration

Create a custom `llms.json` with only the providers you want:

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
        "llama3.3:70b": "llama-3.3-70b-versatile"
      }
    }
  }
}
```

Then mount it:

```bash
docker run -p 8000:8000 \
  -v $(pwd)/custom-llms.json:/home/llms/.llms/llms.json:ro \
  -e GROQ_API_KEY="your-key" \
  ghcr.io/servicestack/llms:latest
```

## Health Checks

The Docker image includes a health check that verifies the server is responding.

### Check Container Health

```bash
docker ps
```

Look for the health status in the STATUS column.

### View Health Check Details

```bash
docker inspect --format='{{json .State.Health}}' llms-server | jq
```

## Multi-Architecture Support

The published Docker images support:
- `linux/amd64` (Intel/AMD x86_64)
- `linux/arm64` (ARM64/Apple Silicon)

Docker automatically pulls the correct image for your platform.

## GitHub Container Registry

Images are automatically published to GitHub Container Registry on:
- Push to main branch → `ghcr.io/servicestack/llms:main`
- Tagged releases → `ghcr.io/servicestack/llms:v2.0.24`
- Latest tag → `ghcr.io/servicestack/llms:latest`

### Pull Specific Version

```bash
docker pull ghcr.io/servicestack/llms:v2.0.24
```

### Pull Latest

```bash
docker pull ghcr.io/servicestack/llms:latest
```

## Troubleshooting

### Container Won't Start

Check logs:
```bash
docker logs llms-server
```

### Permission Issues

The container runs as user `llms` (UID 1000). If mounting local directories, ensure they're writable:
```bash
mkdir -p llms-config
chmod 777 llms-config
```

### Port Already in Use

Change the host port:
```bash
docker run -p 3000:8000 ...
```

### API Keys Not Working

Verify environment variables are set:
```bash
docker exec llms-server env | grep API_KEY
```

## Security Considerations

- Container runs as non-root user (UID 1000)
- Only port 8000 is exposed
- No unnecessary packages installed
- Multi-stage build reduces attack surface
- Health checks ensure service availability

## Performance

- Multi-stage build keeps final image small
- Layer caching speeds up rebuilds
- aiohttp provides async performance
- Health checks prevent routing to unhealthy containers

