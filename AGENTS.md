# AGENTS.md — Codebase Blueprint & Architectural Guide

> **Audience**: AI Models (Claude, GPT, Gemini, Cursor, Copilot, Codex) and human developers working on the `llms.py` (`ServiceStack/llms` / `llmspy` / `AI.Chat`) repository.
> **Purpose**: Provides an authoritative blueprint of codebase architecture, design patterns, core invariants, current capabilities, and development conventions.

---

## 1. Project Overview & Philosophy

`llms.py` is a lightweight, privacy-centric CLI, OpenAI-compatible server, and ChatGPT/Open WebUI alternative for interacting with Large Language Models.

### Core Principles
1. **Zero Runtime Bloat**: Uses only Python standard library and `aiohttp`. No heavy AI frameworks (no LangChain, LlamaIndex, PyTorch, Transformers). Keep the runtime fast, portable, and minimal.
2. **Pure ESM Frontend**: The UI is written in Vue 3 using native browser ES Modules (ESM) and Tailwind CSS. There is **no compilation or bundler build step** (no Vite/Webpack required to run or extend).
3. **Local Privacy & Offline First**: SQLite database persistence per-user (`~/.llms/user/{username}/`), SHA-256 local file caching (`~/.llms/cache`), and first-class offline model support (Ollama, LMStudio).
4. **Modularity & Extensibility**: Features are packaged as ComfyUI-style extensions with server lifecycle hooks and frontend component slots.
5. **Durable Agent Architecture**: Background agent runs execute in bounded stages with database leasing, interrupt recovery, and non-destructive context compaction.

---

## 2. Codebase Directory Blueprint

```
ServiceStack/llms/
├── llms/                         # Main Python package
│   ├── main.py                   # Single-file functional core (~6k lines)
│   ├── db.py                     # Legacy / core database access helpers
│   ├── providers.json            # 530+ model definitions merged from models.dev
│   ├── providers-extra.json      # Provider overrides and custom provider definitions
│   ├── llms.json                 # User & default provider/model configuration
│   ├── index.html                # Single-page application entry point
│   ├── extensions/               # Pluggable modular feature extensions
│   │   ├── app/                  # Durable AgentScheduler, canonical chat_message schema, thread store
│   │   ├── agents/               # Agent profiles (SYSTEM.md, templates, dynamic memory, footer actions)
│   │   ├── gemini/               # Gemini File Search Store RAG, bidirectional sync, assistants API
│   │   ├── core_tools/           # Sandboxed code execution (Python, JS, TS, C#), calc, grep, fetch_url
│   │   ├── computer/             # Anthropic computer-use tools (bash, edit, filesystem, screen)
│   │   ├── browser/              # Headless browser automation tools
│   │   ├── skills/               # Agent Skills standard (SKILL.md progressive disclosure)
│   │   ├── tools/                # Tool discovery, registry API, and function execution endpoint
│   │   ├── voice/                # Audio transcription (Whisper, Voxtral) and TTS support
│   │   ├── pdf/                  # PDF Studio: live Typst (.typ) template editing and PDF compilation
│   │   ├── gallery/              # Media gallery for generated images and audio
│   │   ├── projects/             # Multi-project workspaces and publish paths
│   │   ├── publish/              # Public sharing of threads and media to ai.llmspy.org
│   │   ├── credentials/          # API key & secrets management
│   │   ├── github_auth/          # GitHub OAuth authentication & multi-user isolation
│   │   ├── katex/                # KaTeX LaTeX math rendering extension
│   │   └── analytics/            # Telemetry and query logging
│   └── ui/                       # Core frontend SPA (Vue 3 ESM)
│       ├── App.mjs               # Root layout, sidebar navigation, top bar, dynamic slot rendering
│       ├── ctx.mjs               # AppContext and ExtensionScope reactive state management
│       ├── ai.mjs                # API client (chat completions, SSE streaming, file uploads)
│       ├── modules/
│       │   ├── chat/             # Chat interface (ChatBody.mjs, SettingsDialog, prompt box)
│       │   ├── model-selector.mjs# Searchable, filterable modal for 530+ models
│       │   └── layout.mjs        # Responsive layout and panel states
│       └── lib/                  # Vendored frontend libraries (Vue 3, marked, highlight.js, chart.js, idb)
├── tests/                        # Comprehensive test suite (asyncio, scheduler, streaming, tools)
├── docs/                         # Technical documentation and specs
│   ├── AGENTS.md                 # Agent Profile directory and configuration specification
│   ├── SKILLS.md                 # Agent Skills open standard specification
│   ├── DURABLE_AGENTS.md         # Durable agent run engine architecture & invariants
│   └── RAG_IMPROVEMENTS.md       # Knowledgebase & support assistant proposal
├── pyproject.toml                # Package metadata, ruff linter configuration, entry points
├── Dockerfile                    # Container definition
└── README.md                     # Project splash & quickstart
```

---

## 3. Core Architectural Concepts & Invariants

### 3.1 The Four Conversation Representations
When working on chat history, agent loops, or compaction, understand this fundamental invariant:

1. **Canonical Conversation (`chat_message` table)**:
   - Complete, sequence-addressable history.
   - Append-oriented. Tool calls and results form atomic logical units.
   - Marked with an `active` flag for non-destructive branching.
   - **Never mutated or truncated by model context constraints.**
2. **Compatibility Projection (`thread.messages`)**:
   - JSON array on the `thread` row used for backwards compatibility with legacy UI/API clients.
3. **Model Context Projection**:
   - The latest `context_snapshot` summary plus recent canonical tail messages.
   - Bounded by the model's token context window.
4. **Provider Payload**:
   - A deep-copy formatted strictly for the target model (OpenAI, Anthropic, Gemini, Ollama).
   - Can normalize tool outputs or convert unsupported media to text placeholders without altering canonical history.

### 3.2 Durable Agent Execution (`AgentScheduler`)
Located in `llms/extensions/app/__init__.py`:
- Runs asynchronously in the web process; work is not tied to a single open HTTP connection.
- Uses `agent_run` table for lease-based claiming, step counts, and bounded execution slices.
- Survives process restarts: interrupted runs are automatically reclaimed on startup.
- Automatically initiates non-destructive context compaction when token thresholds are exceeded.
- Communicates real-time progress via Server-Sent Events (SSE) with seamless long-polling fallback.

### 3.3 Extension Architecture
Extensions live in `llms/extensions/<name>/` and can define:
- `__init__.py`:
  - `__install__(ctx)`: Registers routes (`ctx.add_get`, `ctx.add_post`), tools (`ctx.register_tool`), providers (`ctx.add_provider`), and UI script/CSS assets.
  - `__parser__(parser)`: Adds custom CLI flags to `llms`.
  - `__run__(ctx)`: Runs standalone CLI command logic.
- `ui/index.mjs`:
  - Exports `{ install(ctx) }`.
  - Injects Vue components into layout slots: `left` (sidebar), `top` (header), `leftTop` (above chat history), or overrides global components (`ctx.components({ MyComponent })`).

---

## 4. Key Systems & Feature Guide

### 4.1 Model & Provider Management
- **Catalog**: Sourced from [models.dev](https://models.dev) into `providers.json`.
- **Dynamic Selection**: Resolves models by ID, short name, or display name (e.g. `gpt-4o`, `claude-3-7-sonnet`, `gemini-2.5-flash`).
- **Update Command**: `llms --update-providers` fetches the latest models and merges them with `providers-extra.json`.

### 4.2 Function Calling & Tools
- Tools are standard Python functions registered with `ctx.register_tool(fn, group="group_name")`.
- Parameter types, descriptions, and required fields are **auto-generated** from Python type hints and docstrings (`function_to_tool_definition` in `main.py`).
- Built-in sandboxed tools in `core_tools`:
  - `run_python`: Runs Python inside temporary directories with CPU time and virtual memory `ulimit` restrictions.
  - `run_javascript` / `run_typescript`: Runs scripts via `bun` or `node`.
  - `run_csharp`: Executes single-file C# via `dotnet run`.
  - `calc`: Evaluates AST math expressions securely (no `eval`).
  - `fetch_url`: Downloads HTML and converts it to clean Markdown via zero-dependency `HTMLToMarkdownParser`.
  - `grep_search`: Fast regex/pattern file search across allowed directories.

### 4.3 Agent Skills Standard
- Follows the [agentskills.io](https://agentskills.io) standard (`docs/SKILLS.md`).
- Skills are folders containing `SKILL.md` (YAML frontmatter + instructions), with optional `scripts/`, `references/`, and `assets/`.
- Uses progressive disclosure: loads only name + description into system prompt context until activated by the model.

### 4.4 RAG & Knowledge (Gemini File Search)
- Implemented in `llms/extensions/gemini/`:
  - Bidirectional reconciliation (`sync`) between local SQLite and remote Gemini file search stores.
  - SHA-256 deduplication and change detection.
  - Asynchronous background worker (`UploadWorker`) for batch uploads.
  - Inline byte-range grounding citations (`groundingSupports` and `groundingChunks`) linked to source URLs.

### 4.5 Multimodal Capabilities
- **Image Generation**: Supported across Google, OpenAI, OpenRouter, Chutes, and Nvidia.
- **Audio Generation (TTS)**: Native support for Gemini Preview TTS models; files cached locally.
- **Voice Transcription (STT)**: Handles `.webm`, `.wav`, `.mp3` via Groq, OpenAI Whisper, or Mistral Voxtral endpoints.
- **PDF Studio**: Interactive live editor using Typst (`.typ`) with JSON data bindings and live preview.

---

## 5. Development, Testing & Contribution Rules

### 5.1 Rules for AI Models Modifying this Codebase
1. **Never introduce heavy dependencies**: Do not import `pandas`, `langchain`, `transformers`, `torch`, `scikit-learn`, or similar packages into `pyproject.toml`. Stick to the Python standard library and `aiohttp`.
2. **Never break the ESM frontend**: The UI relies on native browser modules. Do not add JSX, TypeScript compilation, or bundler-specific syntax into `llms/ui/`.
3. **Preserve Database Invariants**: Never mutate canonical `chat_message` rows to solve token limit issues; always use the compaction projection pattern.
4. **Cross-Platform Awareness**: Handle Windows path separators (`os.path.normcase`, `os.path.realpath`) and provide non-bash fallbacks where possible (see `core_tools/_code_execution_env`).
5. **Always add clickable file links**: When explaining changes, use Markdown links formatted with the `file://` scheme.

### 5.2 Common Commands

```bash
# Run server locally on port 8000
./llms.sh --serve 8000

# Run in debug/verbose mode
DEBUG=1 ./llms.sh --serve 8000 --verbose

# Run test suite
python -m unittest discover tests

# Run specific test
python -m unittest tests/test_agent_scheduler.py

# Lint and format with Ruff
ruff check . --fix
ruff format .
```

### 5.3 Database Locations
- Default data root: `~/.llms/`
- User databases: `~/.llms/user/{username}/app.sqlite` (or `gemini.sqlite`)
- Shared cache: `~/.llms/cache/{hash_prefix}/{hash}.{ext}`
