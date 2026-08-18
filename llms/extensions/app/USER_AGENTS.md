# Long-running agents in AI.Chat

AI.Chat can now keep an agent working through large tasks that require many model responses, tool
calls, tests, file changes, and verification steps. The latest architecture makes these runs more
durable, responsive, and practical without turning every conversation into a huge browser or database
payload.

## What changed

Previously, an agent's work was closely tied to one completion request. A sufficiently large task could
eventually reach the tool-iteration limit, exceed the model's context window, or make the thread too
large for the browser to render comfortably.

Agent work now runs as a durable background run. Sending a message queues the work and returns control
to the UI immediately. The server continues the task in bounded stages, saving progress between them.
Reaching an internal iteration boundary is now a checkpoint and continuation point instead of the end
of the task.

## Benefits

### Longer, more capable agent threads

An agent can perform many rounds of reading, editing, running commands, inspecting results, and fixing
issues. Work is divided into durable slices, so one request no longer has to remain open for the entire
task. An overall safety budget still prevents an accidental endless run.

### Progress survives normal interruptions

Run state, model/tool progress, errors, and messages are persisted. Refreshing the browser does not own
or cancel the work. If the application shuts down while an agent is active, interrupted work can be
requeued when the service starts again instead of being left permanently "working" in memory.

### Automatic context management

AI.Chat tracks how much of the selected model's context window is being used. As a thread approaches
its limit, older history is summarized into a continuation-safe snapshot while recent messages and
important instructions remain available verbatim.

Compaction is non-destructive: it changes the bounded context sent to the model, not the visible thread.
You can still inspect the original conversation. During compaction, the UI shows token and part
progress; during normal work, a context donut provides a quick view of current utilization.

### Faster real-time updates

AI.Chat prefers Server-Sent Events (SSE) for prompt status, streamed progress, new messages, completion,
and errors. SSE avoids repeatedly opening update requests and generally delivers changes as soon as the
server publishes them.

In the default `auto` mode, the browser automatically falls back to long polling if SSE is unsupported,
blocked, buffered, or unhealthy on the current network/proxy path. The agent run is independent of the
transport, so changing or reconnecting the update channel does not interrupt the work.

### More responsive long threads

Opening a very large thread no longer loads and renders every message at once. AI.Chat initially shows:

- the first 20 messages, which usually contain the original requirements;
- the latest 100 messages, which show current work and results; and
- one clear gap for the omitted middle.

Use the gap controls to load 100 earlier or later messages when needed. This keeps the thread useful
without freezing the page, transferring multi-megabyte histories on every refresh, or losing access to
older details.

### Accurate message counts and stable live merging

The sidebar count comes from the canonical persisted message rows rather than the number currently
rendered in the browser. Live updates merge by stable sequence, so loading more history does not discard
the ranges you are reading. The temporary message shown immediately after sending is replaced by its
persisted server copy instead of appearing twice.

### Clearer progress and stalled-run feedback

The pending response shows elapsed waiting time once it is useful, rather than displaying `waiting 0s`.
The idle timer resets whenever new progress or a message arrives. The "taking longer than expected"
warning therefore appears only when the run has actually been quiet for an extended period, not while
the agent is actively producing updates.

The sidebar shows a useful working state and message count instead of an ambiguous internal step label.
Provider errors and server shutdown errors are persisted and shown after refresh rather than leaving a
thread hanging indefinitely.

### Safer model and attachment compatibility

The model receives a provider-specific copy of the conversation, while AI.Chat retains the complete
canonical history. This allows strict text-only models to receive text placeholders for images, audio,
files, or image-producing tool results without corrupting the stored conversation.

Tool calls and their results are kept together across checkpoints, compaction, and message pages. This
is especially important for long coding runs, where losing or separating one tool result can make the
next model request invalid.

## What you will see during a run

1. Your message appears immediately and the thread enters a working state.
2. Status and streamed messages arrive over SSE or long polling.
3. Long tasks may briefly show `Continuing…` as a completed slice is checkpointed and the next begins.
4. If context needs reduction, the status changes to `Reducing context` with token and part progress.
5. The same thread continues automatically after compaction.
6. Completion, cancellation, or an error is stored and remains visible after refresh.

Internal continuation and compaction checkpoints are implementation details; they do not represent new
user requests and should not appear as repeated synthetic chat messages.

## Cancelling and retrying

Use **Cancel** when a run no longer appears useful. Cancellation stops the active local work and records
the run as cancelled. If a provider or tool fails, the error is attached to the thread and the retry
action starts a new durable run using the available canonical history.

Retry does not require duplicating or manually copying the conversation. Compatibility repair is applied
only to the provider-bound view when older malformed tool history needs to be recovered; the stored audit
history is not silently rewritten.

## Configuration

No configuration is required. Real-time updates default to automatic transport selection:

```json
{
  "defaults": {
    "events": {
      "transport": "auto"
    }
  }
}
```

Available values are:

- `auto` — recommended; try SSE and fall back to long polling when necessary;
- `sse` — require/retry SSE; and
- `long-poll` — disable SSE and always use long polling.

Routes are fixed and do not need configuration. Deployments can keep the minimal `transport` setting;
timeout, heartbeat, failure-threshold, and retry-delay defaults are already provided.

## Practical guidance

- Keep the original requirements and success criteria in the first message; AI.Chat deliberately keeps
  the start of the thread easy to access.
- Let automatic compaction run. It is designed to preserve the recent working state while bounding the
  provider request.
- Use the context indicator as an explanation of model-context pressure, not as a measure of task
  completion.
- A rising message count indicates continued activity, but not necessarily percentage complete.
- Cancel a run if it has shown no new messages or status changes for a long period and the idle warning
  appears.
- Use retry for a persisted provider/tool error. Refreshing alone reconnects the UI but does not create
  another run.

## Current scope

Durability protects agent orchestration and persisted progress. It cannot guarantee that an external
tool operation is safe to repeat unless that tool itself supports idempotency. The current scheduler is
optimized for one AI.Chat application process; multi-process worker ownership and tool-level external
write reconciliation are planned hardening areas.
