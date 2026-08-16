# Running llms-py in Docker

The Docker image bundles llms.py with everything its extensions need — Python,
`bun`, the .NET SDK and `typst` — so nothing has to be installed on the host
beyond Docker itself.

- [Quick start](#quick-start) — the one-line installer
- [What the installer does](#what-the-installer-does)
- [The `llms` command](#the-llms-command)
- [Configuration](#configuration)
- [Running without the installer](#running-without-the-installer)
- [Building and testing locally](#building-and-testing-locally)
- [Troubleshooting](#troubleshooting)

## Quick start

```bash
curl -fsSL https://llmspy.org/install.sh | bash
```

This pulls the latest image, installs an `llms` command on your PATH, and opens
a setup screen where you pick which providers to enable and paste in API keys.
Then:

```bash
llms up                    # start the server on http://localhost:8000
llms ls                    # list enabled providers and models
llms "what is 2+2?"        # ask the default model
```

Re-run the same command any time to update to the latest image — your config and
API keys are left alone:

```bash
curl -fsSL https://llmspy.org/install.sh | bash
```

### Installer options

Pass options after `| bash -s --`:

```bash
curl -fsSL https://llmspy.org/install.sh | bash -s -- --port 3000
```

| Option | Description |
| --- | --- |
| `--no-setup` | Don't open the provider setup screen |
| `--no-pull` | Skip pulling the image |
| `--setup-only` | Just re-open the provider setup screen |
| `--image IMAGE` | Image to use (default `ghcr.io/servicestack/llms:latest`) |
| `--port PORT` | Host port for the server (default `8000`) |
| `--bind ADDR` | Host address to publish on (default `127.0.0.1`) |
| `--dir DIR` | Config directory (default `~/.llms`) |
| `--bin-dir DIR` | Where to install the `llms` command |
| `--uninstall` | Remove the command and container (keeps your config) |

## What the installer does

1. Checks Docker is installed and running, with platform-specific install hints if not.
2. Pulls `ghcr.io/servicestack/llms:latest` and reports whether anything changed.
3. Runs `llms --init` in a throwaway container to create `~/.llms/llms.json` and
   `~/.llms/providers.json` (skipped if they already exist).
4. Writes `~/.llms/config`, `~/.llms/.env` and an optional `~/.llms/docker-compose.yml`.
5. Installs `~/.llms/bin/llms` and `~/.llms/bin/llms-setup`, and symlinks `llms`
   into `~/.local/bin` (or `/usr/local/bin`, or `~/bin`).
6. Opens the provider setup screen.

Nothing is written outside `~/.llms` and the bin directory, and nothing needs `sudo`.

### If you already have the pip package installed

If a different `llms` is already on your PATH — usually `pip install llms-py` —
the installer says so and additionally installs the Docker version as
`llms-docker`, leaving your existing `llms` as the winner. Use whichever you
prefer; they share nothing except the `~/.llms` config directory.

### Provider setup screen

`llms setup` (or the installer) opens a terminal picker listing every provider in
`providers.json`:

```
 llms.py — providers

 ●  Anthropic            ANTHROPIC_API_KEY          saved  sk-a..7Yq2      13
 ○  Cerebras             CEREBRAS_API_KEY           -      -                3
 ●  Groq                 GROQ_API_KEY               shell  gsk_..1f9c      15
 ○  Ollama               -                          local  -                0
 ○  OpenAI               OPENAI_API_KEY             -      -               47

 ↑↓ move  space toggle  enter set key  x clear  a all  n none  s save  q quit
```

The **source** column tells you where each key came from:

| Source | Meaning |
| --- | --- |
| `saved` | Already in `~/.llms/.env` — pre-selected |
| `shell` | Found in the environment you launched from — pre-selected, and copied to `~/.llms/.env` when you save |
| `local` | A provider that runs on your own machine, no key needed |
| `-` | No key yet — press <kbd>enter</kbd> to paste one |

Keys detected in your shell are pre-selected so the common case is one keystroke
(<kbd>s</kbd>). `GITHUB_TOKEN` is the exception: it's listed but never
pre-selected, because it's usually the `gh` CLI's token rather than a Copilot
subscription.

Saving writes API keys to `~/.llms/.env` (mode `600`) and flips
`providers.*.enabled` in `~/.llms/llms.json`. Re-run it any time — it always
shows your current state, so it doubles as a way to see which providers are
configured.

Enabling a local provider (Ollama, LM Studio) also rewrites its `api` URL from
`localhost` to `host.docker.internal`, since `localhost` inside a container is
the container itself.

## The `llms` command

The wrapper passes any llms CLI arguments straight through to the container, so
the rest of the documentation applies unchanged:

```bash
llms ls                              # list enabled providers and models
llms ls anthropic                    # filter to one provider
llms --check groq                    # verify a provider's models
llms -m gpt-5 "explain monads"       # pick a model
llms --image ./photo.png "describe"  # the current directory is mounted at /work
```

It also adds container management commands:

| Command | Description |
| --- | --- |
| `llms up [port]` | Start the server in the background (`--restart unless-stopped`) |
| `llms down` | Stop and remove the server |
| `llms restart` | Restart the server |
| `llms status` | Show whether the server is running, and its health |
| `llms logs [-f]` | Show server logs |
| `llms setup` | Re-open the provider picker |
| `llms update` | Pull the latest image and restart if running |
| `llms shell` | Open a shell inside the container |
| `llms uninstall` | Remove the command and container |

`llms --serve [port]` is accepted as an alias for `llms up` so copy-pasted docs
work.

### Notes on the wrapper

- **Current directory** is mounted at `/work` and set as the working directory,
  so relative paths in `--image`, `--audio` and `--file` work. Absolute host
  paths outside the current directory won't resolve.
- **Port binding** defaults to `127.0.0.1`, so the server is not exposed to your
  network. Set `LLMS_BIND=0.0.0.0` in `~/.llms/config` to change that.
- **Host services** are reachable at `host.docker.internal` (e.g. Ollama at
  `http://host.docker.internal:11434`).

Every setting can be overridden per-command or edited in `~/.llms/config`:

```bash
LLMS_PORT=3000 llms up
LLMS_IMAGE=ghcr.io/servicestack/llms:v4.0.10 llms ls
```

## Configuration

Everything lives in `~/.llms`, which is bind-mounted into the container at
`/home/llms/.llms`:

| File | Purpose |
| --- | --- |
| `llms.json` | Providers, models, defaults — edit freely |
| `providers.json` | Provider/model catalogue from models.dev |
| `providers-extra.json` | Extra providers and models |
| `.env` | API keys, one `VAR=value` per line, mode `600` |
| `config` | Settings for the `llms` command (image, port, bind address) |
| `docker-compose.yml` | Optional, generated — an alternative to `llms up` |

Because the container reads these from the mount, editing `llms.json` on the host
takes effect on the next `llms restart`.

### API keys

Keys are read from `~/.llms/.env` and passed to the container with
`--env-file`. That file's format is strict:

```bash
GROQ_API_KEY=gsk_...
OPENAI_API_KEY=sk-...
```

No quotes, no spaces around `=`, no `export`. `llms setup` writes it correctly;
if you edit it by hand, keep to that format.

Which env var each provider uses comes from `providers.json` — see
[`.env.example`](.env.example) for the full list.

## Running without the installer

Nothing above is required; the image works standalone.

### docker run

```bash
docker pull ghcr.io/servicestack/llms:latest

docker run -d --name llms \
  -p 127.0.0.1:8000:8000 \
  -v ~/.llms:/home/llms/.llms \
  --add-host=host.docker.internal:host-gateway \
  -e OPENROUTER_API_KEY="sk-or-..." \
  ghcr.io/servicestack/llms:latest
```

One-shot CLI use:

```bash
docker run --rm -v ~/.llms:/home/llms/.llms \
  -e GROQ_API_KEY="gsk_..." \
  --entrypoint llms ghcr.io/servicestack/llms:latest ls
```

### docker compose

```bash
cp .env.example .env      # then fill in your keys
docker compose up -d
docker compose logs -f
docker compose down
```

The bundled `docker-compose.yml` uses `env_file: .env`, so it picks up every key
in that file without needing an entry per provider.

### Available tags

Published to GitHub Container Registry and Docker Hub on every push to `main`
and every `v*` tag, for `linux/amd64` and `linux/arm64`:

| Tag | Description |
| --- | --- |
| `ghcr.io/servicestack/llms:latest` | Latest release |
| `ghcr.io/servicestack/llms:4.0.10` | A specific version |
| `ghcr.io/servicestack/llms:4.0` | Latest 4.0.x |
| `ghcr.io/servicestack/llms:main` | Latest `main` build |

### Custom config files

Mount individual files read-only to pin them:

```bash
docker run -p 8000:8000 \
  -v $(pwd)/my-llms.json:/home/llms/.llms/llms.json:ro \
  -v $(pwd)/my-providers-extra.json:/home/llms/.llms/providers-extra.json:ro \
  ghcr.io/servicestack/llms:latest
```

Or extract the defaults first, edit, then mount the whole directory:

```bash
./docker-extract-configs.sh config
# edit config/llms.json
docker run -p 8000:8000 -v $(pwd)/config:/home/llms/.llms ghcr.io/servicestack/llms:latest
```

## Building and testing locally

```bash
./docker-build.sh                    # builds llms-py:latest
./docker-build.sh v4.0.10            # with a tag

docker compose -f docker-compose.local.yml up -d --build
```

### Testing an image

`scripts/test-docker.sh` pulls the image and exercises it end to end — metadata,
multi-arch manifest, non-root user, toolchain versions, `llms --init`,
`llms ls`, a live HTTP server, and Docker's own health check:

```bash
./scripts/test-docker.sh                        # test the published latest
./scripts/test-docker.sh --image llms-py:dev    # test a local build
./scripts/test-docker.sh --no-pull              # skip the pull
./scripts/test-docker.sh --quick                # skip the server tests
./scripts/test-docker.sh --keep                 # leave the container running
```

It exits non-zero if any check fails, so it works as a release gate. If a
provider API key happens to be in your environment it also runs a live
`llms --check` against that provider.

## What's in the image

| Tool | Used for |
| --- | --- |
| Python 3.11 + llms-py | the CLI and server |
| `bun` / `bunx` | JavaScript extensions and tools |
| .NET SDK 10 | running C# code |
| `typst` | the PDF Studio extension (`.typ` templates → PDF) |
| `ffmpeg` | audio conversion for voice input |
| `git` | installing extensions from a repo |

Each is verified at build time, so a broken image fails to build rather than
failing on your first request.

## Voice input

The microphone lives in the browser, not the container — the chat UI records
audio and POSTs it to `/transcribe`, so no device passthrough is needed. The
container's only job is turning that audio into text.

The voice extension's `api` mode needs nothing installed, so it works in the
container out of the box. Add a key and restart:

```bash
echo 'GROQ_API_KEY=gsk_...' >> ~/.llms/.env
llms restart
```

Configure the provider and model under `defaults` in `~/.llms/llms.json`:

```json
{
  "defaults": {
    "voice": {
      "provider": "groq",
      "model": "whisper-large-v3",
      "language": "en"
    }
  }
}
```

Or point it at a local speech-to-text server — no key required. From inside the
container the host is `host.docker.internal`:

```json
{
  "defaults": {
    "voice": {
      "url": "http://host.docker.internal:8001/v1/audio/transcriptions",
      "model": "Systran/faster-whisper-small"
    }
  }
}
```

This is the same configuration llms.py uses everywhere, not something
Docker-specific — see [Voice Input](https://llmspy.org/docs/features/voice-input)
for every setting, the `LLMS_TRANSCRIBE_*` environment overrides, and the other
modes.

Check which mode was selected:

```bash
llms restart --verbose && llms logs | grep -i voice
```

```
Using api for voice: groq [llms.json] model=whisper-large-v3 [llms.json]
```

The image also ships `ffmpeg`, which the `voxtype` and `transcribe` modes need
to convert the browser's webm recording. `voxtype` requires a graphical desktop
session so it never applies in a container; `transcribe` is available if you
mount your own script at `/usr/local/bin/transcribe`.

### The microphone button is missing

Browsers only expose `getUserMedia` in a **secure context**: HTTPS, or
`http://localhost` / `http://127.0.0.1`. The default `llms up` binds to
`127.0.0.1`, so it works.

If you set `LLMS_BIND=0.0.0.0` and browse to `http://192.168.x.x:8000`, the
browser silently withholds the microphone API and no button appears — nothing to
do with Docker or your configuration. Reach it over an SSH tunnel
(`ssh -L 8000:localhost:8000 host`) or put it behind a TLS-terminating reverse
proxy.

### Logging

`llms` reads two environment variables, both off by default:

| Variable | Effect |
| --- | --- |
| `VERBOSE=1` | request/response logging (same as `--verbose`) |
| `DEBUG=1` | debug logging |

The `llms` command turns them into container env vars for you:

```bash
llms up --verbose          # request logging
llms up --debug            # verbose + debug
llms up --debug -f         # ...and follow the logs
llms restart --debug       # turn it on for a running server
llms --debug ls            # one-shot commands too
llms logs                  # last 200 lines
llms logs -f               # follow
llms status                # shows the active log level
```

To make it permanent, set `LLMS_VERBOSE=1` or `LLMS_DEBUG=1` in `~/.llms/config`.

Running the image directly, pass them as normal env vars:

```bash
docker run -e VERBOSE=1 -e DEBUG=1 -p 8000:8000 \
  -v ~/.llms:/home/llms/.llms ghcr.io/servicestack/llms:latest
```

### Passing your own environment variables

Everything in `~/.llms/.env` is passed into the container, not just API keys, so
it doubles as a place for any setting the container should see:

```bash
echo 'TZ=Australia/Perth' >> ~/.llms/.env
llms restart
```

`llms setup` preserves anything there that isn't a provider API key.

## Troubleshooting

**`docker: command not found` / daemon not running**
The installer prints the right command for your platform. On macOS you need
Docker Desktop actually launched, not just installed.

**Permission denied writing to `~/.llms` (Linux)**
The image runs as UID 1000. If your UID is different, the installer detects it
and adds `--user $(id -u):$(id -g)` — stored as `LLMS_DOCKER_USER_ARGS` in
`~/.llms/config`. If you're running Docker by hand, add that flag yourself.

**Voice input isn't working**
Check which mode the extension picked:

```bash
llms restart --verbose && llms logs | grep -i voice
```

`Cannot use api - no voice provider configured` means no key and no `voice`
section — add one. If no microphone button appears at all, see the secure-context
note above.

**PDF Studio isn't available**
It disables itself when `typst` isn't on PATH. Check the image has it:

```bash
llms shell -c 'typst --version'
```

If it's missing you're on an image built before typst was added — run
`llms update`.

**C# code fails with "Couldn't find a valid ICU package"**
An old image without `libicu`. Run `llms update`. As a stopgap you can disable
globalization instead:

```bash
echo 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1' >> ~/.llms/.env && llms restart
```

**A provider is enabled but its models don't appear**
Check the key is actually reaching the container:

```bash
llms setup --list      # shows every provider, its env var, and where its key came from
llms --check groq      # asks the provider directly
```

**Ollama / LM Studio on the host aren't reachable**
Inside a container `localhost` is the container. Use
`http://host.docker.internal:11434` — `llms setup` rewrites this for you when you
enable a local provider.

**Port already in use**

```bash
llms down
LLMS_PORT=3000 llms up      # or set LLMS_PORT in ~/.llms/config
```

**Start over**

```bash
llms down
rm -rf ~/.llms
curl -fsSL https://llmspy.org/install.sh | bash
```

## Security notes

- The container runs as non-root (UID 1000) and only exposes port 8000.
- The server binds to `127.0.0.1` by default — it is not on your network unless
  you set `LLMS_BIND`.
- `~/.llms/.env` is written mode `600`.
- Images are published with build provenance attestations.
