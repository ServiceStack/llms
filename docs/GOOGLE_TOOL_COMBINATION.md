# Gemini File Search with function tools

The Python Google provider sends File Search and function declarations together for Gemini 3 and 4
models, including `gemini-flash-latest` and `gemini-flash-lite-latest`. Combined requests set
`toolConfig.includeServerSideToolInvocations=true` and default to `VALIDATED` function calling.
OpenAI `tool_choice=auto` also uses `VALIDATED`; `none`, `required`, and named choices map to
`NONE`, `ANY`, and `ANY` with `allowedFunctionNames`, respectively.

The feature is Preview. See Google's
[tool combination guide](https://ai.google.dev/gemini-api/docs/generate-content/tool-combination)
and [function calling configuration](https://ai.google.dev/api/caching#FunctionCallingConfig).

## Capability overrides

`tool_combination` is an optional JSON boolean, resolved in this order:

1. The chat request (or a durable thread's `args.tool_combination`).
2. Model metadata, configurable through `providers-extra.json`.
3. The Google provider configuration in `llms.json`.
4. The model defaults described above.

Set it to `true` for a supported custom alias, or `false` to keep retrieval-only behavior. Older
models keep the existing File Search-only behavior and log why function declarations were omitted.
API errors never trigger an automatic retry that silently removes function tools.

File Search-only and functions-only requests do not enable circulation by default. Once circulated
parts are present in the same model's history, subsequent requests keep the flag enabled, even if the
current tools no longer include custom functions. Explicitly disabling support on such history raises
an error instead of discarding its required state.

## Message and persistence contract

Assistant messages retain their normal OpenAI-compatible `content` and `tool_calls`, plus:

```json
{
  "provider_metadata": {
    "google": {
      "provider": "google",
      "model": "gemini-3.8-flash",
      "content": {"role": "model", "parts": []},
      "include_server_side_tool_invocations": true
    }
  }
}
```

`content` above is the complete Gemini candidate content. Streaming collects candidate zero's parts in
arrival order, including standalone signatures and unknown fields. Each checkpoint gets an independent
copy. Final answers and responses containing only built-in activity also retain this metadata.

For the same provider ID and requested model ID, replay uses this raw content once, without separately
rebuilding the compatibility text or function calls. Only `functionCall` enters the local executor.
Built-in `toolCall` and `toolResponse` parts are already executed by Gemini. Function responses use
the returned call IDs and are grouped into one user turn for parallel calls. Older responses without
IDs use the compatibility call's synthesized ID in the outgoing copy.

Another model uses the normal compatibility projection. Other providers strip `provider_metadata` and
`tool_combination` from their outgoing requests. Provider formatting works on a deep copy of history.

Canonical `chat_message` JSON and `thread.messages` retain the metadata without a database migration.
The thread-level diagnostic `providerResponse` is not the replay source. Context estimates count raw
Gemini parts once, including opaque context. Summarization input excludes replay metadata, while retained
signed messages remain intact. Snapshot sequence boundaries keep custom calls with all their results.
Canonical history is never rewritten by compaction. An indivisible signed recent exchange can exceed
the preferred compaction target; it must not be made smaller by truncating signatures.

## Citations

`groundingMetadata` remains separate, per-message citation metadata. Built-in File Search parts can
contain only IDs and tool types; they cannot be used to invent citation sources or spans. Existing
grounding handling remains in place for streaming and non-streaming responses.

## Verification and C# port

Run the regression suites from the repository root:

```sh
.venv/bin/python -m unittest tests.test_google_tool_combination tests.test_google_streaming \
    tests.test_app_db_provider tests.test_agent_scheduler tests.test_stream_history_preservation \
    tests.test_chat_threads_refactor
```

For live verification, set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) and run:

```sh
.venv/bin/python scripts/check-google-tool-combination.py
```

The script makes billable requests. It creates its own temporary File Search store, uploads a document
containing a random code, checks retrieval followed by custom verification and a final answer in both
response modes, and compares citation availability with File Search-only requests. It deletes its store
in `finally`. The default models are `gemini-3.8-flash`, `gemini-3.5-flash-lite`, and
`gemini-3.1-pro-preview`; override with `--models`.

Live verification passed on 2026-10-20 after API credits were restored:

| Model | Non-streaming | Streaming | Combined citations | File Search-only citations |
| --- | --- | --- | --- | --- |
| `gemini-3.8-flash` | Passed | Passed | Present in both modes | Present in both modes |
| `gemini-3.5-flash-lite` | Passed | Passed | Present in both modes | Present in both modes |
| `gemini-3.1-pro-preview` | Passed | Passed | Present in both modes | Present in both modes |

All six cases retrieved the randomly generated document code, invoked the custom verification function
with that exact code, replayed the model's tool context with a matching function response, and returned
the code in the final answer. Each combined exchange exposed two built-in tool parts. The temporary
File Search store was deleted after the run. These checks establish citation availability for this
fixture; they do not guarantee citations for every prompt or validate browser rendering.

The [machine-readable results](checks/google-tool-combination-2026-10-20.json) record this run.
The Python behavior is now verified for the C# port on these three models in both response modes.

The C# port must preserve the same per-message raw-content contract, execution boundary, function IDs,
stream checkpoints, provider isolation, and compaction boundaries. Porting only the payload flag is
insufficient.
