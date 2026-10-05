# Chat threads, projects and the prompt

How conversations are grouped into projects, titled, drafted and composed. Read this before changing the
chat sidebar, the chat prompt (composer), thread metadata, title generation, project membership, or how
agent runs choose their workspace. The same UI and contracts are shared with the C# port
(ServiceStack.AI.Chat), which receives UI changes through its `sync.sh`.

## What users see

- **Sidebar**: chats grouped into project folders under **Projects**, each showing its five most recent chats
  with **Show more** loading ten more. Chats without a project are listed under **Recents**. Rows are one
  line; hovering shows a details card and actions (delete with an inline confirm, folder menu, new chat in
  folder). A folder appears once it has a chat with messages, or an unsent draft.
- **Prompt**: a floating composer with a chip row for **project · profile · model**, attachment thumbnails,
  a textarea that grows with its content, and an action row (attach, settings, voice, send/stop). Dropping
  files anywhere on the chat shows **Drop to attach**.
- **Drafts**: every chat keeps its own unsent text, attachments and edit state, across navigation and
  reloads. Unsent new chats show in the sidebar with a **Draft** badge.
- **Titles**: a new chat gets a prompt-prefix title immediately, then a short generated title shortly after,
  unless the user renamed it.

## Where the code lives

| Area | Files |
|---|---|
| Sidebar component | `llms/extensions/app/ui/ProjectThreads.mjs` (registered as `ThreadsSidebar`) |
| Thread store, selection, deletion, new threads | `llms/extensions/app/ui/threadStore.mjs` |
| Sidebar/thread APIs, chat submission, run workspace, change signal | `llms/extensions/app/__init__.py` |
| Coordination between run submissions and Git writes | `llms/workspace_operations.py`, `llms/extensions/git/operations.py` |
| Thread schema, sidebar queries, metadata writes | `llms/extensions/app/db.py` |
| Title generation | `llms/extensions/app/titles.py` |
| Run workspace scope (`ContextVar`) | `llms/execution_context.py`, used by `AppExtensions.get_allowed_directories` in `llms/main.py` |
| Project identity, visibility, workspace resolution | `llms/extensions/projects/__init__.py`, `llms/extensions/projects/ui/index.mjs` |
| Composer, send flow, attachments | `llms/ui/modules/chat/index.mjs` (`ChatPrompt`, `useChatPrompt`) |
| Drafts | `llms/ui/modules/chat/draftStore.mjs` |
| Project chip and picker | `llms/ui/modules/chat/ComposerContextBar.mjs` |
| Profile chip | `AgentSelector` in `llms/extensions/agents/ui/index.mjs` (via `ctx.setComposerTop`) |
| Model chip and picker dialog | `llms/ui/modules/model-selector.mjs` |
| Floating layout, drop overlay | `llms/ui/modules/chat/ChatBody.mjs` |
| Profile/theme switching | `changeProfile`, `selectTheme`, `userTheme` in `llms/ui/ctx.mjs` |
| Legacy membership recovery | `scripts/recover-project-threads.py` |

Tests: `tests/test_chat_threads_refactor.py`, `tests/test_projects.py`, `tests/test_chat_drafts.mjs`,
`tests/test_draft_persistence.mjs`, `tests/test_plan_action.mjs`.

## Invariants

Keep these when changing anything above.

1. **Project membership belongs to the thread** (`thread.projectId`, NULL = Recents). Changing the project
   of an existing chat moves the whole conversation; it never relabels individual messages or rewrites history.
2. **Moves are rejected while the thread has a queued, running or approval-waiting run**, so a durable run
   never changes workspace midway. Moves are compare-and-set on `membershipVersion` (409 on conflict).
3. **Runs use the workspace captured when they were queued** (`agent_run.workspace`), never a browser
   preference or the user's global "active project". It's applied through `workspace_scope` for the run's
   duration, so concurrent runs of one user in different projects are isolated. If a run's project or folder
   disappears, the run fails visibly instead of falling back to another workspace.
4. **A thread without a project** uses the default no-project policy: no directories in llms-py (matching
   no active project), the host's `ToolsConfig.AllowedDirectories` in C#.
5. **Clients can't set ownership or revision fields**: `metadataVersion`, `membershipVersion`, `titleSource`,
   `titleStatus`, `titleVersion`, `titlePromptSequence`, `lastActivityAt`, `lastSubmissionId`, `user` are
   stripped from create/PATCH payloads. `projectId` is validated against the user's own projects.
6. **Metadata writes never touch history**: rename, move, title completion and reconciliation update the
   `thread` row only, never `messages` or `chat_message`, and don't change `lastActivityAt`.
7. **Sidebar order is conversation activity** (`lastActivityAt`), updated only by accepted turns and run
   progress, never by title or membership changes.
8. **A generated title never replaces a title the user set.** Existing titles are kept (`legacy`); only
   `fallback` titles are replaced, and only for the `titleVersion` they were generated for.
9. **Drafts are browser-private**: never part of thread APIs, exports, canonical messages or publishing.
10. **Async work stays with the draft that started it**: uploads, voice transcription and sends capture
    their origin draft before awaiting, so a late completion can't land in the chat now on screen.

## Data model

All schema changes are additive and applied on startup.

`thread` columns: `projectId`, `lastActivityAt`, `metadataVersion`, `membershipVersion`, `titleSource`
(`placeholder` | `fallback` | `generated` | `manual` | `legacy`), `titleStatus` (`idle` | `pending` |
`complete` | `failed` | `skipped`), `titleVersion`, `titlePromptSequence`, `lastSubmissionId`. Index
`(user, projectId, lastActivityAt DESC, id DESC)` serves the sidebar.

`agent_run.workspace`: JSON `{projectId, directories}` resolved server-side when the run is queued.

Existing rows are backfilled: `titleSource='legacy'`, `lastActivityAt` from `updatedAt`/`createdAt`,
`projectId` stays NULL (a project can't be inferred reliably). `scripts/recover-project-threads.py` can
assign legacy threads to a project when a saved tool result names exactly one project folder (dry run by
default; `--apply` backs up the database first).

`projects.json` entries get a stable `id` (UUID, assigned on first read and persisted) and an optional
`showInSidebar`. Names and folders can change without changing identity; saves match by `id`, then by name
for payloads without one. Writes are serialized and atomic. Deleting a project with an active run returns
409; chats of deleted projects move to Recents (`reconcile_projects`, idempotent).

Project array order determines folders and picker order. Archived projects remain in the full list
for reconciliation and workspace resolution, but are excluded from the manager's active list, project
pickers and the main sidebar, including draft-only folders. Archiving forces `showInSidebar=false` and
records its previous value in `archivedSidebarVisibility`; unarchiving restores that value and appends
the project to the active list. It never changes chat membership, activity, messages or a run's workspace.
Metadata/bulk saves preserve server-owned archive state; omitted archives survive older active-only saves.

## APIs (`/ext/app`, `/ext/projects`)

| Route | Purpose |
|---|---|
| `GET /ext/app/thread-sidebar` | Visible project groups (5 rows each) + Recents (30 rows) + `revision` |
| `GET /ext/app/thread-sidebar/threads?projectId=…&limit=10&cursor=…` | Next page of a project; `scope=unassigned` for Recents |
| `GET /ext/app/thread-sidebar/updates?sig=…` | Long-poll: returns `{revision}` when it differs from `sig` |
| `GET /ext/app/thread-sidebar/updates/stream?sig=…` | SSE version of the above |
| `POST /ext/app/threads` | Create; accepts `projectId` |
| `PATCH /ext/app/threads/{id}` | Rename (`title`) or move (`projectId` + current `membershipVersion`) |
| `POST /ext/app/threads/{id}/chat` | Submit a turn; `submissionId` makes a resubmission return the accepted state |
| `PATCH /ext/projects/sidebar/{id}` | `{"showInSidebar": bool}` |
| `POST /ext/projects/order` | `{"ids": [active IDs in display order]}`; exact membership required, stale list returns 409 |
| `PATCH /ext/projects/archive/{id}` | `{"archived": bool}`; archive hides folder, restore appends to active list |
| `GET /ext/publish/detect-dist?threadId=…` | Publish directory for the thread's own project |

Pages are keyset-paged on `(lastActivityAt, id)`; the cursor encodes the scope and is rejected for another
scope. Page rows are compact summaries (`id`, `title`, `projectId`, activity, versions, `runStatus`,
`messageCount`, stats) and never include messages. `runStatus` is only set for queued/running/approval runs.

Extension route errors keep their status: raising `web.HTTPBadRequest(text=…)` (400), `HTTPNotFound` or
`HTTPConflict` (409) returns `{"responseStatus": {"errorCode", "message"}}` with that status, so the UI can
show the message (`run_extension_handler` in `llms/main.py`).

## Sidebar updates

Writes that can change the sidebar raise an in-memory signal (`SidebarSignal` in the app extension):
`notify_thread_update()` (titles, moves, activity, streaming, run state), thread create/delete, chat
submission, reconciliation, and project saves (`ctx.notify_sidebar()`). SSE/long-poll subscribers recompute
a user's cached revision only after a signal, at most once a second, so an idle sidebar issues no queries.
The signal is per process.

Client (`ProjectThreads.mjs`): a changed revision triggers `refresh()`, which reloads the first pages and
then refills any group expanded with **Show more** back to its previous length. A change arriving during a
refresh queues one more refresh. The current chat stays visible even when it's beyond the loaded page.

## Titles

`defaults.summarize` in `llms.json` is a normal chat request template (default model `openai/gpt-oss-120b`,
resolved through configured providers; set to `null` to disable). Configs without the key (created before
titles existed, since upgrades don't add new keys) fall back to the packaged `llms.json` template. On the first accepted turn the server sets
a prompt-prefix title (`fallback`, or `Image attachment` for attachment-only prompts). `TitleWorker.enqueue`
claims generation with a conditional `idle → pending` update, then runs a background task: at most two at
once, only the bounded first prompt, no chat filters/tools/persistence, 15s timeout, one retry for
transient failures. There is no job table; titles left `pending` by a restart are regenerated at startup
from the first user message. Results are applied with a conditional update and notify watchers.

## Drafts (`draftStore.mjs`)

A draft is keyed by account + server base + thread ID; a chat that doesn't exist yet uses an opaque
`local:<uuid>` key. `ctx.chat.messageText`, `attachedFiles` and `editingMessage` are writable adapters over
the current draft, so existing extensions keep working. Drafts persist to IndexedDB (`llms-chat-drafts`);
object URLs and File objects aren't persisted, so uploads in progress must be reattached after a reload.

Send flow (`sendUserMessage`): snapshot the draft before any await; for a `local:` draft create the thread
(with the draft's project, or none if that project no longer exists), then transfer the draft to the new
thread ID. If creation fails the draft is left untouched and the project list is reloaded. After the server
accepts the turn, only the sent snapshot is cleared; text typed meanwhile is kept. Provider completion never
clears a draft. Conflicting edits from another tab are kept as recovery drafts.

New chat in a folder (or selecting a project in the project manager) opens that project's most recent
unsent draft, or starts one, so the folder appears in the sidebar with a prompt ready.

## Composer

- Chip row: `ComposerContextBar` (project), then components registered with `ctx.setComposerTop()` (the
  agents extension adds the profile chip), then `ModelSelector`. Chip popups and hover cards open upward.
- Project picker: search, **Don't work in a project** (explicit null), **New project** (applies to the same
  draft/thread), and a workspace note for existing chats. Picking for an unsent draft only changes the draft;
  for an existing chat it PATCHes membership.
- The textarea auto-sizes up to 40% of the viewport; `ChatBody` measures the composer with a
  `ResizeObserver` and pads the message list so the last message stays reachable.
- Attachments: `attachFiles(draftKey, files)` shows local previews immediately and uploads in the
  background. Upload metadata keeps the local file's name and type, because uploads are content-addressed
  and `createContent` chooses image/audio/file by extension.

## Profiles and themes

A profile changes the theme only if it sets one (its `config.json` `theme`, or the user's override in the
profile manager). Any other profile, including Default, applies the user's own theme (`llms.userTheme`,
saved whenever the user picks a theme), so leaving a themed profile restores it. The built-in Chat, Coder
and Planner profiles don't set a theme.

## Verifying UI changes

- Rebuild CSS after adding Tailwind classes: `tailwindcss -i ./llms/ui/tailwind.input.css -o ./llms/ui/app.css`.
- Test hover/click flows with real pointer input (e.g. WebDriver actions). A synthetic `.click()` doesn't
  move focus, which hid a bug where swapping the focused delete button cancelled its own confirmation.
- Browsers heuristically cache `.mjs` files; use a fresh profile or hard refresh after changing them.
- Touch fallbacks use `@media (any-hover: none)`, not `(hover: none)`: touchscreen laptops report
  `hover: none` for their primary pointer even when a touchpad or mouse is in use.

## Known limits

- The sidebar change signal and C#'s equivalent are per process.
- Project reads/writes are serialized per process; there's no cross-process transaction between chat
  acceptance and project deletion.
- Legacy threads stay in Recents unless recovered, and in llms-py run without project directories.
- Multi-tab drafts are conflict-safe but not live-synchronized.
- The model selector is only available where the chat prompt is shown.
