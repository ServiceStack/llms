# Durable agent architecture

This document describes the durable agent implementation in `llms-py` and the invariants that must
be retained when porting it to AI.Chat's C# implementation. For a user-facing overview, see
[`USER_AGENTS.md`](USER_AGENTS.md).

## Goals

The architecture is designed to let an agent continue across many model/tool exchanges without tying
its lifetime to one HTTP request, one browser connection, or one provider context window. It provides:

- durable, resumable run state in the RDBMS;
- an append-oriented canonical conversation independent of model context projection;
- bounded execution slices instead of a terminal maximum-tool-iterations failure;
- automatic, non-destructive context compaction;
- responsive thread rendering for histories containing thousands of messages;
- SSE updates with reliable long-poll fallback; and
- provider-bound normalization without mutating persisted or in-memory agent history.

The current worker is deliberately in-process. It uses asynchronous tasks in the web process, not an
OS thread or separate worker process. The database remains authoritative so the execution strategy can
later be moved to ServiceStack background jobs or another worker without redesigning the data model.

## Architectural model

There are four distinct representations. They must not be collapsed into one object:

1. **Canonical conversation** — active `chat_message` rows are the complete user-visible and auditable
   history. They are append-oriented and sequence-addressable.
2. **Compatibility projection** — `thread.messages` remains during migration for existing clients and
   APIs. It is not the preferred paging source.
3. **Model context projection** — the latest `context_snapshot` plus the canonical tail forms a bounded
   provider context. It may omit or summarize history but never rewrites the conversation.
4. **Provider payload** — a deep copy of the model projection, normalized for one provider/model. It
   may merge legacy messages, remove internal fields, or convert unsupported resources. It must never
   mutate the agent's authoritative working history.

This separation is the central durability invariant. Compaction, provider compatibility, pagination,
and streaming UI state are projections over the canonical conversation, not replacements for it.

## RDBMS schema

### `agent_run`

One row represents one user-requested agent execution.

| Column | Purpose |
| --- | --- |
| `id`, `threadId`, `user` | Identity and ownership. |
| `status` | `queued`, `running`, `completed`, `failed`, or `cancelled`. |
| `nextAction` | Durable indication of the next worker action. |
| `model` | Selected model for diagnostics and recovery. |
| `stepCount`, `sliceCount`, `maxSteps` | Overall progress and hard run budget. |
| `contextTokens`, `contextLimit` | Current projected context usage for UI and compaction. |
| `leaseOwner`, `leaseExpiresAt` | Exclusive worker ownership and recovery. |
| `nextAttemptAt` | Reserved retry/backoff scheduling. |
| `error` | Terminal failure presented to the thread UI. |
| `createdAt`, `updatedAt`, `completedAt` | Lifecycle timestamps. |

### `agent_step`

One row currently represents a durable model slice. It provides ordered execution evidence and an
idempotency boundary.

| Column | Purpose |
| --- | --- |
| `runId`, `sequence`, `type`, `status` | Ordered step identity and lifecycle. |
| `input`, `output` | Bounded diagnostic/checkpoint metadata. |
| `idempotencyKey` | Unique key such as `run:{runId}:step:{sequence}`. |
| `attempt`, `error` | Retry and failure state. |
| `startedAt`, `completedAt`, `createdAt` | Timing and audit information. |

The next hardening stage should record model calls and tool calls as individual steps rather than one
aggregate slice step.

### `chat_message`

This is the normalized canonical conversation.

| Column | Purpose |
| --- | --- |
| `threadId`, `sequence` | Stable cursor and unique order within a thread. |
| `runId`, `stepId` | Optional provenance. |
| `role`, `message` | Queryable role plus complete message JSON. |
| `timestamp` | Stable message identity used during projected checkpoint reconciliation. |
| `toolCallId`, `toolName` | Tool correlation and diagnostics. |
| `tokenCount` | Approximate per-message context cost. |
| `active` | Active branch marker; old branches remain auditable. |
| `createdAt` | Persistence timestamp. |

Important constraints:

- `(threadId, sequence)` is unique.
- Server-assigned timestamps must be monotonic within the working message list. Several tool callbacks
  can occur in the same millisecond, so `DateTimeOffset.UtcNow` alone is not a sufficient identity.
- An assistant message containing `tool_calls` and its contiguous tool results form an atomic logical
  group. Pagination, compaction, checkpointing, and history repair must not split it.
- Editing, deleting, redo, and explicit history rewriting mark the old active branch inactive and
  create a new active sequence. Automatic compaction never changes `active` history.

### `context_snapshot`

Snapshots are versioned, non-destructive context projections.

| Column | Purpose |
| --- | --- |
| `threadId`, `runId`, `version` | Snapshot identity and provenance. |
| `fromSequence`, `toSequence` | Exact canonical range represented. |
| `summary` | Validated continuation-safe system-context messages. |
| `tokenCount`, `model` | Size and summarizer diagnostics. |
| `createdAt` | Creation timestamp. |

### Existing `thread` additions

`thread.streamingMessage` stores an in-flight assistant response separately from canonical messages,
so an interrupted stream cannot damage history. `thread.contextTokens` supports thread-level context
display and backfill. Existing lifecycle, error, provider, usage, and metadata fields continue to be
used.

### Migration behavior

`AppDB.init_db()` runs on startup. It creates the four durable tables and indexes with
`CREATE TABLE/INDEX IF NOT EXISTS`, then inspects existing tables with `PRAGMA table_info` and adds
missing columns. Existing databases therefore migrate naturally when opened by the new version; no
manual migration command or destructive rebuild is required.

Legacy `thread.messages` data is retained. Normalized rows are backfilled/synchronized as threads are
used, allowing old and new clients to coexist during the compatibility period. The C# port should use
the equivalent additive, idempotent ServiceStack OrmLite migration and should not drop the JSON column
until every reader and mutation path uses normalized rows.

## Run lifecycle and scheduler

Posting a chat message does not execute the agent inside the HTTP request:

1. The endpoint validates ownership and rejects a second active run for the thread.
2. Canonical user messages and thread settings are persisted.
3. An `agent_run` is inserted as `queued`.
4. The scheduler is signalled and the endpoint immediately returns the updated thread/run DTO.
5. A bounded worker claims the run, creates an `agent_step`, constructs context, and executes one slice.
6. A final model response completes the run. Reaching the per-slice tool iteration limit raises
   `AgentSliceYield`, checkpoints the new messages, and returns the run to `queued` for another slice.

`metadata.maxSteps` is the overall run budget and defaults to 250. `limits.max_iterations` is only a
slice boundary for durable runs; stateless completions retain the terminal error because they have no
durable state from which to resume.

### Claiming and leases

`AgentScheduler` atomically claims up to `defaults.agent.maxConcurrency` runs (default 2), changes
them from `queued` to `running`, assigns its owner ID, and sets a lease. Active runs renew their lease
periodically. Completion, failure, cancellation, or yield clears ownership.

Startup requeues rows left `running` by an interrupted instance. Graceful shutdown cancels active
tasks and returns still-running work to `queued`. The implementation currently assumes one web
process. A multi-process C# host must use owner-aware expired-lease recovery and an atomic
compare-and-set claim in the database.

### Idle behavior

All enqueue paths signal an in-memory wake event. While work is active, the coordinator polls at the
configured interval to observe slice completion and refill capacity. With no active work it waits on
the event and performs no once-per-second queue query. This preserves durable scheduling without
creating constant idle RDBMS traffic.

For C#, use a singleton hosted service plus `Channel`, `SemaphoreSlim`, or an async reset event. Do not
use a dedicated OS thread. Model, tool, and database operations are asynchronous I/O; additional
threads add synchronization and shutdown complexity without improving throughput.

## Checkpointing invariants

Tool progress is persisted while a slice is running. The projection sent to the model may contain a
snapshot summary not present in canonical history, so it must never replace `thread.messages`.

Checkpoint reconciliation uses the set of baseline message timestamps, not a message-list offset.
Provider normalization can legitimately merge or remove projected messages, making positional indexes
unsafe. Before reconciliation, every provider-created tool result receives a monotonic timestamp.
Only identities absent from the baseline/previous checkpoint are appended to canonical history.

The C# port must retain these rules:

- initialize the identity set after the request filter has timestamped the projection;
- timestamp assistant and tool messages before reconciliation;
- update the identity set after every successful append;
- append only newly observed messages;
- annotate appended rows with `runId` and `stepId`; and
- never persist snapshot summaries or other projection-only messages.

## Provider isolation and compatibility

Provider preparation operates on a deep copy. It removes internal fields such as `timestamp`,
`_sequence`, `streaming`, local model metadata, and usage fields only from the outbound payload. The
working agent history keeps them for durable reconciliation.

Text-only models receive string content exclusively. Multipart text is flattened, while images,
audio, files, and tool resources become compact textual placeholders. Top-level tool resource fields
must be consumed before generic provider code can expand them back into multipart content.

The GLM compatibility projection additionally:

- merges adjacent ordinary same-role messages;
- ensures system context appears only at the beginning;
- preserves complete assistant-tool/result groups;
- removes incomplete historical tool envelopes while retaining useful prose;
- converts orphaned tool results into ordinary contextual text;
- removes repeated tool groups/call IDs created by legacy checkpoint bugs; and
- adds a neutral continuation user turn when recovering an interrupted trailing assistant state.

These repairs are outbound projections. They do not silently rewrite the audit history.

## Context accounting and compaction

Before each slice, `context_for_run()` constructs the working context from the latest snapshot and all
active messages after `toSequence`. It records `contextTokens` and the selected model's `contextLimit`
on the run.

Automatic compaction normally triggers at 80% of the model context limit. If model metadata lacks a
limit, the fallback threshold is 80,000 approximate tokens. Per-thread metadata can override:

- `compactThreshold` — trigger token count;
- `compactChunkTokens` — maximum summarizer batch size, default 60,000; and
- `compactRecentMessages` — verbatim recent tail, default 12 and minimum 4.

Automatic and manual compaction share one provider-neutral service. It:

1. Preserves authoritative leading system/developer instructions.
2. Separates a recent verbatim tail.
3. Partitions older history without splitting tool groups.
4. Bounds oversized individual message/resource values.
5. Makes non-persisting (`nohistory`, `nostore`) structured summarization requests.
6. Validates roles, string content, non-empty output, token budget, and actual reduction.
7. Hierarchically reduces large results for up to four passes.
8. Consolidates chunk outputs into one synthetic system-context summary.

Automatic compaction writes a new snapshot and continues the same thread. Manual compaction uses the
same engine but intentionally creates a child thread. The complete visible conversation remains
available in the original thread in both cases.

## Thread update transport

Routes are fixed; only transport behavior is configurable under `defaults.events`:

```json
{
  "defaults": {
    "events": {
      "transport": "auto"
    }
  }
}
```

If the section is absent, transport defaults to `auto`. Effective defaults are:

```json
{
  "transport": "auto",
  "longPollTimeoutSeconds": 25,
  "sseHeartbeatSeconds": 15,
  "sseConnectTimeoutSeconds": 5,
  "sseFailureThreshold": 3,
  "sseRetryDelaySeconds": 10
}
```

Supported values are `auto`, `sse`, and `long-poll`.

- `auto`: the browser opens SSE. A connection timeout, unsupported `EventSource`, or repeated health
  failures causes that watcher to fall back to long polling.
- `sse`: the browser retries SSE after the configured delay and does not intentionally fall back.
- `long-poll`: the browser uses long polling and the server rejects the SSE endpoint.

The check is primarily client-side because only the browser can know whether the complete path through
its runtime, proxy, buffering, authentication, and network supports a healthy event stream. Server
heartbeats let the client detect a connected-but-buffered/stalled stream. Both transports observe the
same persisted thread state; neither owns execution, so switching transport cannot stop a run.

SSE provides faster updates and avoids a request per update cycle. Long polling remains easy to port,
proxy-friendly, and operationally reliable. The C# implementation should retain identical routes,
configuration names, heartbeat semantics, and fallback state machine.

## Scalable thread reads and rendering

Thread lists no longer deserialize every multi-megabyte history. Sidebar queries return authoritative
`messageCount` plus only the latest preview message.

Opening a thread returns up to 20 leading and 100 trailing normalized messages. The omitted middle is
represented by one sequence-aware gap. Users can load the next or previous 100 messages. Each page has
a soft 512 KiB ceiling (server maximum 2 MiB), and tool groups are never split.

The browser merges pages and live snapshots by `_sequence`; an optimistic timestamp-only message is
replaced by its sequenced server copy. At most 800 messages are retained for rendering, preserving the
edge the user is exploring. Explicit mutation operations fetch full canonical history before rewriting
so hidden messages cannot be accidentally deleted.

This is bounded rendering, not full DOM virtualization. If richer arbitrary scrolling is later needed,
add a virtualized list on top of the same cursor/window API rather than returning full history again.

## Streaming and progress UX

In-flight assistant text is stored separately in `thread.streamingMessage` and merged only in DTOs.
It becomes canonical on successful response completion. Errors and shutdowns clear/replace this state
so a stale partial cannot masquerade as a completed message.

The UI exposes useful persisted progress rather than an ambiguous step number:

- message count and run state in thread lists;
- elapsed wait time for the current pending response, reset whenever a message/update arrives;
- a warning only after a genuinely idle interval, not while messages continue flowing;
- context usage as a transparent-centre donut whose fill changes with utilization; and
- expanded token and chunk progress while compaction is running.

## Cancellation and failure handling

Cancellation marks the active run `cancelled`, records completion, and cancels the local task when this
process owns it. Provider errors, context errors, and server shutdowns are persisted on both run/thread
state and therefore survive refresh. Retry creates a new durable run over canonical history; provider
repair can project legacy malformed history safely without rewriting it.

## C# porting plan

Implement the C# version in this order:

1. Add the four tables, indexes, thread columns, DTOs, and additive OrmLite migration.
2. Normalize/backfill messages and implement sequence-window queries plus branch-safe rewrites.
3. Add run creation, atomic claim/lease operations, step persistence, and cancellation.
4. Implement a singleton asynchronous hosted scheduler with bounded concurrency and idle wake-up.
5. Convert the current completion loop into resumable slices and checkpoint every assistant/tool turn.
6. Enforce stable message identities and deep-copy provider payload preparation.
7. Port the shared compaction service and snapshot-based context projection.
8. Add fixed SSE/long-poll routes and the same `defaults.events` client fallback behavior.
9. Port bounded head/tail thread DTOs, sequence paging, merge logic, and UI progress indicators.
10. Add regression fixtures for shutdown recovery, slice yield, parallel tools, compaction, malformed
    legacy history, text-only resources, pagination boundaries, and SSE fallback.

Do not begin with UI changes or SSE alone. The canonical-message, run, lease, checkpoint, and context
projection invariants are the foundation that makes every UI/transport improvement safe.

## Required regression invariants

The port should prove at minimum:

- a user submission returns before the agent finishes;
- exceeding slice iterations queues another slice rather than failing;
- restart requeues interrupted work without duplicating committed messages;
- parallel tool calls retain all call/result pairs exactly once;
- provider normalization cannot change checkpoint identities;
- text-only models never receive multipart or top-level resource fields;
- compaction cannot alter visible canonical history;
- legacy databases migrate without data loss;
- head/tail paging reports the same count as the sidebar;
- optimistic messages reconcile with sequenced messages without duplication;
- SSE failure falls back to long polling in `auto`; and
- idle installations do not poll the run queue once per second.

## Further hardening roadmap

1. Add owner-aware expired-lease recovery for multi-process deployments.
2. Persist model and tool calls as individual steps with tool-level idempotency/reconciliation.
3. Detect repeated identical tool calls, unchanged observations, repeated errors, and no-progress loops.
4. Add wall-time, cost, token, tool-call, retry, and artifact budgets with approval thresholds.
5. Store large tool/provider payloads as artifacts with previews and retention policies.
6. Add cursor-based delta events rather than sending bounded thread snapshots on every update.
7. Persist structured task state: goal, plan, decisions, evidence, risks, and next action.
8. Add provider-native continuation/background/compaction adapters behind the neutral checkpoint model.
9. Add policy-aware tools, scoped approvals, secret redaction, and immutable security audit events.
10. Add traces/evaluations for latency, compaction fidelity, retries, tool errors, completion quality,
    and replayable long-run regression scenarios.
11. Add bounded child/sub-runs with parent-owned synthesis, cancellation, and aggregate budgets.
