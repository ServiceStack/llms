# OpenAI subscription authentication

`openai_auth` connects one ChatGPT registration per llms-py user. It is independent of the
application's own login and does not read another user's grant or the operator's Codex profile.
The UI remains shared byte-for-byte with C# AI.Chat.

The implementation now follows the public [Sign in with ChatGPT registration flow](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
and [Responses inference contract](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference):

- Fresh PKCE S256, state and OIDC nonce, with a stable installation host ID. Initial registration uses
  `dynamic_agent_client`; subsequent sign-ins use the issued client ID bound to the verified subject.
- Automatic local callbacks: a loopback listener starts before authorization, preferring port 1455
  and selecting an available port if occupied. The exact selected redirect is retained for exchange.
  State binds each callback to its initiating user; PKCE, OIDC verification, ten-minute expiry,
  duplicate-parameter rejection and one exchange per attempt still apply.
- RS256 signature verification using the configured issuer's JWKS, with issuer/audience/authorized
  party/subject/expiration/not-before/nonce checks. JWT-supplied key URLs are never used. Signature
  verification uses strict RFC 8017 PKCS#1 v1.5 encoding and SHA-256, without new runtime dependencies.
- Granted scopes control inference. A valid identity-only sign-in is retained with `plan_enabled=false`;
  it does not use an API key as an inference fallback. Sign in again and authorize plan usage.
- Account-specific models from the public `/v1/models` endpoint using the user's OAuth access token,
  showing only entries with `visibility="list"`, preserving server order and resolving
  display names to the current account's model IDs. Unknown models are rejected rather than silently
  changing the selected model. Unavailable grants hide OpenAI choices while other providers and
  Settings remain accessible.
- Public `/v1/responses`, bearer headers local to each request, `store=false`, `stream=true`.
  Text, images, reasoning and function-call history are translated without changing canonical chat
  history. An explicit `response.completed` is required; EOF, errors and incomplete events fail.
- One refresh/retry after an HTTP 401; uncertain inference failures never restart the outer provider
  loop or switch to a paid provider. A personal subscription grant disables OpenAI API-key access,
  including image/audio generation. Disconnect restores the configured API key and API model catalog
  for that user; the stored key and other users' access are preserved. Invalid or expired grants keep
  API-key access disabled until disconnected. Status distinguishes `has_api_key` (configured) from
  `api_key_active` and `api_key_disabled` (per-user routing).
- Bounded, redirect-free, cookie-free HTTP; strict UTF-8/finite/duplicate-free JSON; redacted failures;
  bounded SSE frames and total stream bytes; socket-read/connect timeouts; cancellation and no-store
  checkpoints. Settings responses use `Cache-Control: no-store` and reject foreign Origin headers.

## Local sign-in callbacks

Select **Continue with ChatGPT** in Settings. The loopback receiver completes sign-in automatically,
closes the sign-in tab, and the settings panel updates through status polling. Callback errors appear
in the initiating user's panel. The receiver binds only to `127.0.0.1`, disables access logging, and
is closed during server cleanup. Return URLs must match the app's origin.

This requires the browser and llms.py to run on the same computer. Remote browsers cannot reach the
server through a loopback URL. Hosts explicitly choosing manual completion can supply
`Options(automatic_callback=False)` through `ctx.openai_subscription_options`; only that mode shows
callback URL entry. It still requires the complete URL, including state and issued client ID.

## Credential lifecycle and deployment

Credentials remain in `user/<user>/credentials/openai_subscription.json`. Writes use an owner-only
`0600` temporary file, fsync and atomic replacement. Credential paths cannot contain symbolic links.
Refreshes are serialized per credential path, including across extension instances in the same
process. Generation and whole-record fingerprints prevent a late refresh, import or callback from
restoring a disconnected grant or replacing a newer connection. Rotating tokens are written together;
proactive refresh failure can retain only the same still-unexpired grant.

Run **one server process per data root**. Atomic file replacement is not a distributed refresh lock.
Multiple independent processes sharing rotating credentials are not supported by this extension.
Windows uses the host filesystem's access controls; the Unix mode guarantee does not establish a
Windows ACL policy.

Disconnect immediately stops local use, removes tokens, and retains only the verified registration
mapping for a later sign-in. It attempts refresh-token revocation using the issuer's discovery endpoint.
If remote revocation cannot be confirmed, the UI tells the user to remove app access in ChatGPT Settings.
It does not automatically retry uncertain revocation calls. A later local sign-in reuses the registration
and installation host ID. Only one selected registration is supported per local user; there is no
multi-account picker within that user partition.

**Existing private Codex grants require a new public sign-in.** They are not silently converted,
automatically imported, shared with another user or used with the new endpoints. A present invalid
or expired grant prevents automatic API billing fallback. The Settings status exposes
`requires_reconnect` so this migration is actionable.

`Options` in `security.py` is host-owned. Production defaults use the official public endpoints.
Endpoint overrides accept HTTPS, with literal loopback HTTP for isolated tests. Browser input cannot
change endpoints or token-storage paths. Local import is disabled unless the host explicitly supplies
both `local_credentials_path(user)` and `can_import_local_credentials(request)`. The legacy
`/import_codex` route name is retained for the shared UI, but only application-issued credentials with
an issued client ID, signed ID token, scopes and expiry are accepted. It never probes `~/.codex/auth.json`.
Treat an import resolver as a grant of access to that user's specific trusted application record.

Retained ID tokens stay on the server. This implementation deliberately omits `id_token_hint` from
browser-facing authorization-link JSON; reauthorization still uses the saved client ID and validates
the returning subject, but does not offer the documentation's hint-based account-selector shortcut.

## Review findings and C# comparison — 2026-10-05

The prior Python implementation had these concrete defects; the current Python implementation fixes them:

| Priority | Trigger and impact | Fix |
|---|---|---|
| P1 | Missing user credentials fell back to admin/default/arbitrary-user files; local import exposed the operator's CLI grant. Requests could consume another account's plan. | Exact user partition; authorized, opt-in application import. |
| P1 | Manual entry accepted a bare code without matching state or flow owner; ID-token payloads were decoded without signature validation. | Owned, one-time full callbacks and verified OIDC identity. |
| P1 | Shared provider/modality headers held bearer tokens; parallel users could mix authentication or retain an old account header. | Per-request headers; subscription tokens never enter API-key modality providers. |
| P1 | EOF and provider error events could become successful responses; outer retries could repeat an uncertain inference or switch billing providers. | Completion required, bounded error-aware SSE, non-retryable subscription errors. |
| P2 | A single global flow and unguarded credential writes allowed concurrent connect/disconnect/refresh to overwrite or resurrect credentials. | Per-user flows, private atomic storage, serialized refresh and generation/fingerprint checks. |
| P2 | Borrowed Codex client IDs, connector scopes, private endpoints, CLI impersonation and silent model substitution diverged from the public application protocol. | Public registration, resource/scopes, Responses endpoint and account model catalog. |

C# already addressed most of these and was the architectural reference, not an unquestioned oracle.
Its current backend still has review items that were **not changed in this Python-focused task**:

- `OpenAiSubscriptionFlow.Credentials` rejects identity-only sign-in instead of retaining the verified
  identity with plan usage disabled. Python now distinguishes identity from permission.
- `OpenAiSubscriptionStore.Disconnect` deletes the registration as well as its tokens, and C# disconnect
  does not attempt remote revocation or report its outcome. Python retains a token-free mapping and
  reports unconfirmed revocation.
- C# proactive refresh fallback checks the time captured before the network request. A token that
  expires while a failed refresh is in flight can be returned as valid; Python rechecks the current time.
- Both implementations use refresh locks within a single process. Neither establishes cross-process
  rotation safety. Both omit browser-visible ID-token hints and support automatic local loopback callbacks.

These observations do not certify either implementation for production or prove live interoperability.

## Verification

```bash
.venv/bin/python -m unittest tests.test_openai_auth tests.test_stream_history_preservation tests.test_agent_scheduler tests.test_mcp_client
node tests/test_openai_auth_ui.mjs
```

The Python suite uses real loopback HTTP and independent OpenSSL-signed ID tokens, disposable user
roots and no production credentials. OpenSSL is a **test prerequisite**, not a runtime dependency.
It covers signed-identity rejection, callback ownership/replay/expiry, separate users, private atomic
writes, late callback/refresh/catalog races, refresh timing/rotation, import authorization, revocation,
model discovery, public request shape, concurrent headers, no-store/cancellation, SSE tools/errors/EOF,
body bounds and the outer chat retry integration.

Initial review results: 36 authentication tests; 129 Python tests including related regressions; UI behavior
checks; 69 existing C# OpenAI/sync tests plus one embedded-asset manifest test. Full sync changed only
`chat/ext/openai_auth/OpenAiSettings.mjs` and `chat/shared-assets.json`; subsequent `--check` found no
drift across 282 assets. The main chat model selector was not changed. Ruff and whitespace checks passed.
Evidence is under `/tmp/openai-auth-review/` and may disappear when the machine's temporary files are cleaned.

Live OpenAI dynamic registration, real JWKS rotation, account eligibility and plan inference were not
exercised. Verify those explicitly with an eligible account before claiming live end-to-end support.
