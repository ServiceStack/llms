# AI Assistant Roadmap: Highest Customer Value Next

Reviewed against the working tree on **8 October 2026**. This is a product recommendation, not a list of missing primitives or a claim that proposed features have shipped.

## Recommendation

Build **grounded web answers**, **persistent artifacts with an editing workspace**, and **private project knowledge usable by any model** next. These address three recurring jobs: finding trustworthy information, producing something usable, and working with the customer's own material.

Follow with better history navigation, explicit memory, and controls for long-running work. Move multi-model comparison below these everyday workflows. Access to many models is an advantage, but customers obtain more value from completing a task than from comparing four answers. Keep voice, inbound MCP, presentations, delegation, and team governance on the roadmap with narrower first releases.

The previous report identified useful directions but treated several existing capabilities as new, prescribed branch relationships absent from the schema, proposed changing the protected chat model selector, and made unsupported promises about latency, zero-setup search, and execution isolation. This revision distinguishes the implemented foundation from the incremental customer feature.

## How to interpret the ranking

**Assumed primary customer:** an individual developer, researcher, writer, or small business user choosing a private, self-hostable assistant with hosted and local models. No customer interviews, retention data, feature-request counts, or willingness-to-pay evidence were supplied. The ordering is a hypothesis based on repository evidence and these customer jobs; confidence is strongest about implementation gaps, weaker about demand.

Evaluate customer value using the original four dimensions, with broader wording:

| Dimension | Weight | Question |
|---|---:|---|
| Frequency and reach | 35% | How often will the assumed customer encounter this need? |
| Task completion | 30% | Does it remove manual work and produce a usable result? |
| Trust and continuity | 20% | Does it reduce unverifiable answers, repetition, or lost work? |
| Product differentiation | 15% | Does it make privacy, portability, or provider choice useful? |

The table below uses qualitative judgments rather than invented numeric scores. **Effort is separate from value:** S means a contained enhancement; M means several coordinated components; L means a new subsystem or substantial lifecycle work. These are relative scope estimates, not delivery dates. Dependencies can change implementation order without changing customer-value rank.

## Repository findings: what already exists

| Area | Evidence in the repository | Remaining customer gap |
|---|---|---|
| Durable work | [Scheduler and routes](llms/extensions/app/__init__.py), [canonical storage](llms/extensions/app/db.py), [durability contract](llms/extensions/app/DURABLE_AGENTS.md) provide leased runs, bounded slices, restart recovery, cancellation, compaction, and SSE/long polling. | User budgets, loop detection, useful task summaries, and completion notifications. Background execution is already implemented. |
| Projects and drafts | [Chat/thread contract](docs/CHAT_THREADS.md), [draft store](llms/ui/modules/chat/draftStore.mjs), [project explorer](llms/extensions/projects/explorer.py). | Reusable project sources and produced artifacts connected to chat. Do not rebuild project folders or draft persistence. |
| Search and branching | [Recents](llms/extensions/app/ui/Recents.mjs) searches titles and messages; `AppDB.query_threads()` uses `title LIKE :q OR messages LIKE :q`. [ChatBody](llms/ui/modules/chat/ChatBody.mjs) supports edit/redo; `rewrite_chat_messages()` preserves inactive canonical rows. | Indexed search with exact message navigation, recovery of alternate histories, and visible versions. `chat_message` has no parent/branch column; `thread.parentId` is a thread relationship, currently used by manual compaction. |
| Generated content | [Workspace file viewer](llms/ui/modules/WorkspaceFileView.mjs) previews source/images, [Git diff view](llms/extensions/git/ui/GitDiffView.mjs) previews changes, and [ChatBody](llms/ui/modules/chat/ChatBody.mjs) renders files, images, audio, structured tool output, and some sandboxed HTML in tool arguments. [PDF Studio](llms/extensions/pdf/README.md) already edits templates/resources with live compilation when Typst is installed. | A common persistent artifact lifecycle and an editor alongside chat. An iframe alone is not a new Canvas product. |
| Retrieval and citations | [Gemini extension](llms/extensions/gemini/__init__.py), [retrieval UI and citations](llms/extensions/gemini/ui/index.mjs), [Google adapter](llms/extensions/providers/google.py). | Model-independent local retrieval and general web search. File-search grounding metadata is not evidence of a universal web search workflow. |
| Code and calculations | [Core tools](llms/extensions/core_tools/__init__.py) already provide Python/JS/TS/C#, `calc`, `grep_search`, and URL-to-Markdown fetching. | Uploaded-file bindings, retained output files, data previews, and reproducible analysis. Execution currently returns stdout/stderr/returncode from temporary directories. |
| Personalization | [Agent profiles](llms/extensions/agents/README.md) and [profile assembly](llms/extensions/agents/__init__.py) support prompt templates, user/personality files, and `MEMORY_LATEST`. | A user-visible, scoped memory manager and deliberate relevance selection. Memory is not starting from zero. |
| External tools | [MCP client](llms/extensions/mcp_client/README.md) already has connection forms, OAuth/bearer credentials, discovery, selection, edited approvals, durable pauses, and uncertain-outcome reconciliation. | Curated setup recipes, task-oriented discovery, and validation against real integrations. Manual JSON editing is not the only current setup path. |
| Sharing and documents | [Sharing contract](docs/SHARING.md), [static sharing](llms/extensions/share_static/README.md), [hosted sharing](llms/extensions/share_llmspy/README.md), and PDF Studio. | Portable conversation exports, redaction previews, and optional revocation/access controls for hosted shares. Public sharing already exists. |
| Voice | [Voice extension](llms/extensions/voice/README.md) supports local transcription tools and compatible hosted endpoints; audio outputs can be rendered in chat. | A continuous conversation session with interruption, transcript continuity, and measured latency. No fixed latency target is established by the audit. |
| Users and usage | [Credentials auth](llms/extensions/credentials/README.md) already has accounts, Admin roles, locking, and account management. App routes/store and ChatBody record/display usage and estimated costs. | Shared team resource permissions and enforced budgets. Do not describe authentication, isolation, or usage reporting as wholly absent. |
| Adjacent product foundations | [Decision Studio](llms/extensions/jev/README.md) already has reusable recipes, examples, immutable run history, and AI-assisted recipe editing. [Desktop packaging](desktop/README.md) already exists. | Reuse these patterns where appropriate; another recipe studio or desktop wrapper would duplicate existing work. |

These findings are from source and repository documentation, not a live certification of every provider or deployment. Existing tests cover scheduler, chat/thread, MCP, projects, core tools, and Decision Studio contracts; their presence is not a claim that all tests were run for this report.

## Ranked roadmap

| Rank | Feature to add or extend | Customer outcome | Expected reach | Effort | Demand confidence |
|---:|---|---|---|:---:|:---:|
| 1 | Grounded web answers, then bounded research | Answer current questions with inspectable evidence | Broad, daily | M → L | Medium |
| 2 | Persistent artifacts and editing workspace | Create, revise, preview, and download usable work | Broad, frequent | L | Medium |
| 3 | Private project knowledge for any model | Ask useful questions about personal/project documents | Broad within privacy-focused users | L | Medium |
| 4 | Indexed history search and recoverable branches | Find and reuse prior work; safely try alternatives | Broad, frequent | S → L | Medium |
| 5 | Explicit personal and project memory | Stop repeating preferences and stable facts | Broad, recurring | M | Medium |
| 6 | Run controls, progress, and completion notifications | Trust an assistant to finish longer tasks within limits | Frequent for agent users | M | Medium |
| 7 | File analysis with retained outputs | Obtain verified calculations, charts, and cleaned datasets | Analysts, developers, business users | M → L | Medium |
| 8 | Guided connectors for real customer workflows | Work with repositories and business information in chat | Broad when integrations match demand | M | Low–medium |
| 9 | Scheduled tasks and meaningful-change monitors | Delegate recurring checks and reminders | Recurring-work users | L | Low–medium |
| 10 | Focused multi-model comparison | Choose a useful answer/model with visible tradeoffs | Power users | M | Low–medium |
| 11 | Portable exports and controlled sharing | Hand off completed work without formatting cleanup | Broad, occasional | S → M | Medium |
| 12 | Continuous voice conversations | Use the assistant naturally while hands are occupied | Mobile/accessibility/voice users | L | Low–medium |
| 13 | Bounded child agents | Complete decomposable tasks with less context noise | Advanced agent users | L | Low |
| 14 | Team resources, permissions, and budgets | Operate a shared assistant for a paying team | Team deployments | L | Low |

For a local-only audience, promote #3 to #1. For an analyst audience, promote #7. For a confirmed team buyer, promote #14. Those changes should follow customer evidence rather than assumptions about the broad assistant market.

### 1. Grounded web answers, then bounded research

**Why first:** freshness and verifiability affect many questions, regardless of provider. A customer should be able to ask about a recent release and inspect the evidence without leaving chat. Search improves evidence availability; it does not eliminate hallucinations.

**First release:** add one dependable search adapter and one self-hosted option, with explicit setup, a search control separate from the model picker, clickable claim-linked citations, and a source drawer containing retrieved excerpts and retrieval timestamps. Reuse `fetch_url` extraction where suitable and the existing citation/content-filter patterns. Store source IDs, URLs, titles, excerpts, and provenance as structured data; the model must cite retrieved IDs rather than invent source links.

**Next:** a durable research run with a visible plan, bounded query/page/time/cost budgets, cancellation, source deduplication, and a downloadable report. Keep ordinary quick search useful before expanding to a multi-stage research agent.

**Integration:** proposed `llms/extensions/search/`; existing [core tools](llms/extensions/core_tools/__init__.py), [scheduler](llms/extensions/app/__init__.py), [content filters](llms/ui/ctx.mjs), and [Gemini citations](llms/extensions/gemini/ui/index.mjs). Any shared citation contract should retain provider-native grounding metadata.

**Constraints:** hosted APIs may require keys and incur charges; a self-hosted service requires configuration. Do not promise zero setup or depend on fragile search-result scraping as the primary service. Use bounded asynchronous HTTP, validate destinations and redirects, and treat retrieved pages as evidence rather than privileged instructions. Citation snippets should distinguish search summaries from fetched page content. [Brave's API reference](https://api-dashboard.search.brave.com/api-reference/web/search/get) documents an available HTTP search integration; provider selection still needs deployment testing.

**Success:** assess a fixed set of current-fact questions for source coverage, whether citations actually support claims, failure handling, latency, and per-answer cost. Measure repeat use, not just search calls.

### 2. Persistent artifacts and editing workspace

**Why next:** an answer becomes more valuable when the customer can save, revise, and use its output. This covers writing, simple web tools, diagrams, and code without making chat an IDE replacement.

**First release:** persistent Markdown/text, HTML, and SVG artifacts; a collapsible preview/editor beside chat; explicit save/download; immutable versions with restore; and a targeted edit action that shows a proposed diff. On mobile, use a full-screen artifact view. Start with a lightweight native ESM editor.

**Shared prerequisite:** define an owned artifact record with stable ID, thread/project/run provenance, MIME type, filename, version, content hash, and retention policy. Keep file data outside message JSON and return small references/previews. Distinguish private artifacts from intentionally published assets. This is a proposed contract, not an existing artifact table.

**Integration:** proposed artifacts extension, [layout](llms/ui/modules/layout.mjs), [App](llms/ui/App.mjs), [ChatBody](llms/ui/modules/chat/ChatBody.mjs), and [PDF Studio](llms/extensions/pdf/README.md). Extend the existing workspace preview navigation and [file viewer](llms/ui/modules/WorkspaceFileView.mjs); reuse [Git diff rendering](llms/extensions/git/ui/GitDiffView.mjs) where appropriate. Delegate Typst/PDF editing to the existing studio. Rendering hooks belong in the frontend; artifact persistence and authorization belong on the server.

**Constraints:** isolate generated HTML in an iframe without same-origin privileges, constrain network access with CSP, and validate messaging against the intended frame. Network permissions must be explicit. Initially support single-document HTML/ESM; arbitrary React/npm projects would require a separate runtime. Workspace application needs a reviewable diff, path authorization, and conflict detection before writing changed files.

**Later:** Mermaid/Chart.js previews, console inspection, viewport toggles, and approved workspace application. Avoid making all of these prerequisites for the first useful editor.

**Success:** customers can create, revise, reload, restore an earlier version, and download an artifact without copy/paste; unauthorized users cannot retrieve it. Track completed/downloaded artifacts and manual handoff steps removed.

### 3. Private project knowledge for any model

**Why here:** local models are more useful when they can retrieve the customer's documents without sending those documents to a cloud retrieval service.

**First release:** add selected project files or uploaded TXT/Markdown/source files to a local index. Show ingestion status, selected sources, and excerpt citations with file/line locations. Support explicit source selection rather than silently indexing an entire workspace. Provide an offline path using a local chat model and local retrieval.

**Retrieval approach:** use SQLite full-text retrieval first, then optional local embeddings and hybrid ranking when evaluation shows a benefit. Probe FTS5 availability and provide a bounded fallback. [SQLite FTS5](https://www.sqlite.org/fts5.html) supplies full-text querying and ranking facilities. [Ollama's embedding endpoint](https://docs.ollama.com/api/embed) accepts text or batches; embeddings can be requested over HTTP without an AI SDK. A native vector extension remains optional and must not become a base installation requirement. Pure Python vector scans suit bounded collections, not unlimited scale.

**Integration:** proposed `llms/extensions/knowledge/` retrieval interface with local storage and an adapter for existing [Gemini retrieval](llms/extensions/gemini/__init__.py). Keep the cloud workflow working while adding a common source-selection/citation contract.

**Constraints:** record chunk provenance, extraction version, embedding model/dimensions, hashes, and document revisions. Deletion and permission changes must remove retrievability. State which models receive retrieved excerpts: local indexing plus a hosted chat model is not an entirely offline workflow. PDF/DOCX/OCR require honest format support and optional extraction tooling; no universal stdlib PDF parser is assumed.

**Success:** answer a curated set of project questions with correct source locations; preserve isolation between users/projects; prove the local path works with external network access disabled; measure retrieval quality and ingestion failures.

### 4. Indexed history search and recoverable branches

**Why here:** customers should find a useful answer and experiment without losing it. Both needs extend current functionality.

**First release A:** improve existing history search with an index over readable active message text, project/date/model filters, relevant snippets, and links that load the exact canonical message. Current search scans serialized `thread.messages` with `LIKE`; preserve its behavior while replacing the query path. Search must find messages outside the currently rendered window.

**First release B:** expose preserved alternative histories through a simple versions/restore view, then add per-turn edit/regenerate navigation. A full conversation tree can follow after the storage contract is proven.

**Integration:** [AppDB](llms/extensions/app/db.py), [Recents](llms/extensions/app/ui/Recents.mjs), [thread store](llms/extensions/app/ui/threadStore.mjs), and [ChatBody](llms/ui/modules/chat/ChatBody.mjs).

**Constraints:** `active` is an audit-preservation mechanism, not a sufficient tree model. Add an explicit branch identity, ancestry/fork position, and active branch selection before promising sibling traversal. Recoverable old rows may lack enough metadata to reconstruct every historical parent relationship; expose honest legacy versions. Branch selection must rebuild the compatibility/model projections, invalidate or scope compaction snapshots, preserve complete tool-call groups, and reject changes during an active run. Never replay prior tool effects merely because a branch was selected.

**Success:** find a phrase in the omitted middle of a long chat; switch between edited versions after reload; retain both prior answers and tool pairs; keep normal sidebar ordering stable. Ship indexed search independently of the larger branching migration.

### 5. Explicit personal and project memory

**Why here:** repeated preferences consume effort across every new chat. Start with understandable controls rather than automatic semantic extraction.

**First release:** “Remember this” for a selected fact; global and project scope; an editable memory list; source provenance; disable/pause memory; and a temporary chat mode that neither reads nor writes memory. Show which memories were used in a response's context details.

**Integration:** extend [agent/profile assembly](llms/extensions/agents/__init__.py) and [Settings](llms/ui/modules/chat/SettingsDialog.mjs). Keep memory distinct from profile instructions, canonical conversation, and compaction snapshots. Capture the applicable project when queuing a run rather than consulting mutable browser state later.

**Later:** suggest facts for confirmation, resolve conflicts, and retrieve relevant memories within a small token budget. Keyword/scope selection can precede embeddings. Avoid learning stable preferences from a one-off request or treating quoted document content as user instructions.

**Success:** preferences apply in a new chat, project facts stay in their project, deletion stops future injection, and temporary chats remain independent. Measure reduced repeated instructions and incorrect-memory reports.

### 6. Run controls, progress, and completion notifications

**Why here:** durability already allows long work; the next value is making that work understandable and bounded.

**First release:** a compact activity summary, a clear distinction between working/waiting for approval/failed/completed, elapsed time, user-configurable run time/tool/token limits, repeated-call/no-progress detection, and opt-in browser completion notifications. Retain expandable raw details; tool cards and cancellation already exist.

**Integration:** [scheduler](llms/extensions/app/__init__.py), [agent tables](llms/extensions/app/db.py), [existing run-hardening plan](llms/extensions/app/DURABLE_AGENTS.md), and [ChatBody](llms/ui/modules/chat/ChatBody.mjs). Record meaningful model/tool steps instead of deriving all progress from an aggregate slice. Notification delivery needs deduplication and ownership checks.

**Constraints:** a web-process scheduler does not run while the server is stopped. Browser notifications are platform/permission dependent. Display uncertain costs as estimates. Monetary limits need usage reconciliation and conservative reservations for parallel calls; estimated catalog prices alone cannot guarantee a precise billing ceiling. Extend MCP's existing uncertain-outcome rules rather than adding transparent retries for side effects.

**Success:** detect a repeated failing tool loop, stop at a configured limit, recover coherent status after reload/restart, and emit one completion notification. Track stalled/abandoned runs and successful bounded completions.

### 7. File analysis with retained outputs

**Why here:** users need actual calculations and deliverables from their files, not another generic code execution tool.

**First release:** bind selected CSV/JSON attachments to a run, inspect schema/sample/missing values with bounded reads, execute calculations, and retain downloadable CSV/JSON results plus tables and declarative Chart.js charts. Show executed code and source files so results can be reproduced.

**Integration:** existing [core executors](llms/extensions/core_tools/__init__.py), [chart library](llms/ui/lib/charts.mjs), and the artifact contract from #2. Capture output files before temporary directories are removed, validate them, and return owned artifact references.

**Constraints:** subprocess timeouts, a stripped environment, and Unix resource limits are useful controls but do not enforce complete filesystem/network isolation; Windows does not apply the same Bash limits. Before exposing arbitrary execution to untrusted multi-user workloads, provide an OS/container isolation backend with explicit capabilities. Keep CSV/JSON useful with stdlib; make XLSX, plotting packages, and advanced analytics optional runtimes with visible availability, not mandatory dependencies.

**Success:** compare totals against known fixtures, reproduce a saved calculation, download a cleaned dataset after reload, and reject access to another user's file. Measure analysis tasks that finish with a usable output.

### 8. Guided connectors for real customer workflows

**Why here:** external tools are valuable when they solve recognizable tasks. The transport and much of the approval UI already exist.

**First release:** curate a small set of setup recipes based on actual demand—start with repository search/read and one customer-requested document service. Supply authentication guidance, a connection test, tool-purpose explanations, and “use this connection in this chat” selection. Advertise tested capabilities rather than every server in a marketplace.

**Integration:** extend [MCP Connections](llms/extensions/mcp_client/README.md) and its existing UI; reuse profile/skill restrictions and approvals. Prefer remote connections compatible with the implemented transport. Local stdio support is a separate addition, not an already available recipe option.

**Constraints:** preserve approval pauses, edited arguments, principal scoping, and `outcome_unknown` reconciliation. Secrets are not universally encrypted by default: MCP provides host protection hooks and documents plaintext standalone storage. Say what is actually protected. Begin with read-oriented workflows and reviewable writes; a connector must never broaden the user's tool scope implicitly.

**Success:** a customer connects a supported service, completes one real task, understands any approval, and recovers from expired authentication. Measure successful tasks per connector and setup abandonment, not catalog size.

**Deferred:** inbound MCP and a public tool marketplace. Inbound serving primarily benefits other clients and has less direct assistant value. If implemented, expose a selected, authorized tool set; use stdio and an explicitly supported Streamable HTTP revision rather than treating legacy HTTP+SSE as the default. The [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) documents this distinction. Inbound Python support must be evaluated separately from the C# port.

### 9. Scheduled tasks and meaningful-change monitors

**Why here:** reminders, recurring reports, and “tell me when this changes” turn the assistant into an ongoing helper. This is different from durable execution of a task submitted now.

**First release:** one-off reminders and scheduled read-only checks, with timezone, next-run time, pause/delete, a run history, and notification only on a meaningful change or failure. Show schedule and scope before activation. Reuse #1/#8 for useful monitored sources.

**Integration:** proposed schedules/occurrences tables feeding the existing durable run queue. Persist the profile, model, project workspace, tool scope, and budget at scheduling time. Enforce a unique occurrence identity, define missed-run behavior, and avoid replaying external effects after recovery. `agent_run.nextAttemptAt` alone is not a recurring schedule model.

**Constraints:** document daylight-saving behavior and server uptime requirements. Revalidate credentials and access when each occurrence runs. Add browser/in-app delivery first; email/mobile push need separate adapters and user setup. Coalesce missed checks rather than flooding the user.

**Success:** a scheduled check runs in its captured workspace, survives restart without duplicate occurrences, remains quiet when unchanged, and stops after pause/delete. Measure recurring tasks that customers retain and unwanted notifications.

### 10. Focused multi-model comparison

**Why lower than before:** it demonstrates provider choice but increases reading effort and spend. Most ordinary requests need one satisfactory result.

**First release:** an explicit Compare action on a prompt, two models chosen in a separate view, independent streamed outcomes, cost/latency estimates, and “continue from this answer” as a child thread with complete provenance. Offer a user-triggered retry with another model before building a four-column arena.

**Integration:** [API client](llms/ui/ai.mjs), app request accounting, and a new comparison view using the existing global picker outside the main chat selector. Durable child runs should own comparison execution; browser promises alone do not preserve it across navigation.

**Constraints:** freeze identical input and context at dispatch. Start without side-effecting tools; parallel model calls must not execute duplicate writes. Report per-column failures without discarding successful answers. Label estimated costs and define latency measurements; tokens/second is not response quality.

**Success:** compare the same input, cancel either run, survive navigation, and continue the chosen answer without merging incompatible tool histories. Measure whether comparisons improve later model choices or completion rates.

### 11. Portable exports and controlled sharing

**First release:** clean Markdown and self-contained HTML conversation/artifact export, with a preview choosing whether to include tool output, reasoning, attachments, and sources. Add explicit redaction review to existing share flows. Reuse PDF Studio for optional Typst exports when available.

**Integration:** [SharePanel](llms/ui/modules/SharePanel.mjs), [share-option registration](docs/SHARING.md), and the two existing sharing extensions. Do not create a new `publish/export.py` path under an extension absent from the current tree.

**Constraints:** secret detection helps but cannot guarantee complete redaction. Clearly state whether a link is public. Revocation and expiry are features of a controlled hosted endpoint; downloaded files and static copies cannot reliably be recalled. Preserve source links and useful formatting in exported reports.

**Later:** DOCX and presentations only after export demand is demonstrated; support them through optional tools or constrained formats without adding heavy base dependencies.

**Success:** exported reports render independently of the running app, contain only selected content, and retain citations. Track deliverable handoffs and export failures.

### 12. Continuous voice conversations

**First release:** complete a continuous record/transcribe/respond/play loop using existing transcription options, with an explicit stop, interruption, text transcript, and clear listening state. Use this to validate demand before adding provider-native realtime sessions.

**Later:** a single realtime provider adapter, then additional providers. Keep transcript/history integration and turn cancellation coherent when interrupted; define which tools and approvals can be used during a voice session.

**Integration:** [voice extension](llms/extensions/voice/README.md), chat audio rendering, and canonical history. Keep local transcription selectable rather than replacing it with a cloud-only route.

**Constraints:** measure time from end of speech to first audible response on target devices and networks. Do not promise sub-500ms latency or attribute a fixed delay to the current implementation without measurements. Realtime audio costs, microphone permissions, reconnect behavior, and accessibility affect usability more than an animated orb.

**Success:** interrupted speech stops promptly, the transcript reflects delivered turns, connection loss is recoverable, and no microphone capture continues after the session closes.

### 13. Bounded child agents

**First release:** deliberate delegation of a read-only research/review subtask to one child, with separate context, a compact result, parent/child status, cancellation propagation, and shared run budgets. Add concurrency only after the single-child lifecycle is reliable.

**Integration:** proposed parent/child run relationships on the durable scheduler; task state and retained artifacts from #2/#6. A child must inherit or narrow captured workspace and tool permissions.

**Constraints:** prevent circular/unbounded spawning and aggregate model/tool costs. Shared writable workspaces are not isolation; require a conflict strategy before parallel editing. Preserve each child's full audit history and provenance while returning a compact summary to the parent.

**Success:** a child improves a decomposable task, parent cancellation stops it, and resource totals remain bounded. Compare completion quality, time, and cost with a single agent before promoting delegation broadly.

### 14. Team resources, permissions, and budgets

**First release, contingent on buyers:** shared read-only knowledge/profile resources with explicit membership and project roles, plus per-user usage limits. Build on existing accounts, isolation, and MCP audience/role policies rather than replacing them.

**Later:** shared writable projects, member administration, audit export, organization billing controls, and managed credential protection. Require a defined team customer and deployment model before committing to real-time co-editing or an enterprise control plane.

**Integration:** [credentials](llms/extensions/credentials/__init__.py), [GitHub auth](llms/extensions/github_auth/__init__.py), [project identity](llms/extensions/projects/__init__.py), and app usage accounting. Permission checks must cover retrieval, artifacts, exports, queued work, and connected tools.

**Success:** revoked members lose access at the next protected operation; two team members can use an approved shared resource without seeing private chats; concurrent runs cannot bypass configured quotas. Measure adoption by paying teams rather than administrator-screen usage.

## Delivery sequence

Customer-value rank is a priority list, not a requirement to finish every large feature before shipping smaller work. Deliver thin releases in this order:

| Stage | Deliverable | Dependencies and release gate |
|---|---|---|
| Foundation and quick wins | Capability/status guidance; baseline task metrics; indexed history search (#4A); portable Markdown export (#11). | Show missing key/runtime requirements honestly. Preserve working chat/drafts. Validate search against long chats. |
| First major product release | Quick grounded search (#1) and basic persistent artifacts (#2). | Shared source/artifact contracts; citations that resolve; private artifact authorization; reload-safe versions. These may ship independently. |
| Own-context release | Local project retrieval (#3), explicit memory (#5), run budgets/progress (#6). | Scoped context selection; offline verification; memory controls; bounded runs. |
| Workflow expansion | Branch versions (#4B), file analysis (#7), guided connectors (#8), bounded deep research (#1 next). | Proven branch migration, output retention/isolation, tested integrations, evidence and budget controls. |
| Recurring and specialist workflows | Schedules (#9), comparison (#10), richer export (#11), voice pilot (#12). | Occurrence deduplication, scoped background access, usable notifications, measured demand. |
| Advanced/team investment | Child agents (#13), team governance (#14), optional inbound MCP. | Task-quality gains or committed buyers; aggregate budgets; clear permission and conflict model. |

**Default next development milestone:** a current-fact question produces a cited answer with inspectable excerpts, and a generated document becomes a persistent editable/downloadable artifact. Build a small end-to-end version of each before investing in a broad research engine or full IDE-style canvas.

## Architecture and release requirements

- Keep the Python base runtime to stdlib and `aiohttp`; isolate optional extraction, execution, and rendering tools. No LangChain, LlamaIndex, PyTorch, Transformers, or Pandas dependency in [pyproject.toml](pyproject.toml).
- Keep the frontend native Vue ESM. Vendor any chosen UI library deliberately; no required bundler/JSX compilation step.
- Preserve the four conversation representations in [DURABLE_AGENTS.md](llms/extensions/app/DURABLE_AGENTS.md). Compaction and provider normalization must not alter canonical history. Explicit branching needs its own projection and snapshot contract.
- Follow [CHAT_THREADS.md](docs/CHAT_THREADS.md): capture workspace when queued, reject moves during active runs, keep metadata edits separate from message history/activity ordering, retain origin draft ownership, and use in-memory sidebar signals.
- **Never change [the main chat model selector](llms/ui/modules/model-selector.mjs)** for comparisons, search, or shared picker styling. Place new controls elsewhere. Keep one focus indicator and stable control dimensions.
- Enforce user/project ownership on sources, memories, artifacts, downloads, scheduled work, and tool bindings. A content hash is not authorization. Public media and private uploaded/generated files need distinct access policies.
- Treat process resource limits and permissions as explicit capabilities; do not describe them as complete security isolation. Preserve MCP approvals and uncertain-outcome handling when extending agent execution.
- Preserve shared Python/C# UI contracts. Add additive storage migrations and targeted regression coverage for affected behavior; verify representative deployments and optional-runtime absence.

## Validate the priorities before scaling them

Run short customer trials around five concrete jobs: research a current topic; create and revise a deliverable; query private project documents; find an old answer and try a variation; analyze a CSV. Observe manual work, failures, evidence quality, and whether the customer returns to repeat the task.

Collect only opt-in, content-free task metrics where possible. Establish baselines before choosing numerical targets. Re-rank after the first releases using repeat use, successful deliverables, time saved, support burden, and stated willingness to pay. Repository capability and competitor feature availability establish feasibility and context; they do not establish customer demand.
