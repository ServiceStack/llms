# Outbound MCP connections

The `mcp_client` extension ports the C# AI.Chat outbound MCP client to Python. It is enabled by
default, uses only `aiohttp` beyond the Python standard library, and shares the C# client's routes,
selection aliases, connection screen, approval form, and result format.

The source UI is `ui/index.mjs`. In ServiceStack.AI.Chat, run:

```sh
./sync.sh --extension mcp_client /path/to/llms/llms
```

The normal full `sync.sh` also copies it. Both copies must remain byte-identical. Changes to this
shared UI belong here first. The existing inbound C# MCP server is a separate feature; importing
remote tools does not expose them through another MCP server.

## Extension-scoped connection files

In an authenticated host, shared connections live in
`~/.llms/user/default/mcp_client/config.json`; personal connections live in
`~/.llms/user/<username>/mcp_client/config.json`. Without authentication, the default account
manages its own connections in the default file through the UI. Both use:

```json
{
  "servers": [{
    "id": "knowledge",
    "displayName": "Company Knowledge",
    "endpoint": "https://knowledge.example.com/mcp",
    "auth": { "mode": "anonymous" },
    "allowedTools": ["search", "read_document"]
  }]
}
```

An `llms-py` server loads the MCP Connections section on `/tools` without any preconfigured
servers. Without host authentication, the default account runs in single-user mode. To override
the default behavior in a regular installation, set this in `llms.json`:

```json
"mcp_client": { "enabled": false }
```

This turns off both MCP endpoints and the Tools-page section. An embedding host can instead
configure the extension before installation:

```python
app.mcp_client_config = {
    "enabled": True,
    "oauthRedirectUri": "https://chat.example.com/ext/mcp_client/oauth/callback",
}
```

Host policy (`limits`, `networkPolicy`, `singleUserMode`, OAuth callback URL) can be set in
`llms.json` under `mcp_client`, or in `app.mcp_client_config` for an embedding host. The host
override takes precedence. `singleUserMode` defaults to true only when host authentication is
absent; an explicit setting can override it. Secrets and custom
authorization use the host hooks below. Code-defined shared `servers` may also be supplied by
an embedding host; their IDs must not conflict with files.

Use **Tools → MCP Connections → Add connection** to create personal connections; **Settings** and **Connection options → Remove connection**
manage them. The form asks for Name, Server URL, and Authentication, generates the ID, and connects
after saving. All tools are discoverable by default, with per-call approval still required. Authentication is shown in the main form; host-managed tool restrictions remain available in configuration files. OAuth uses `oauthClientId`, `oauthIssuer`,
and optional `oauthScopes`. OAuth providers such as GitHub also require a client secret; enter it in the form, where it is sent to `POST /connections/{id}/oauth-client-secret` and stored in the MCP database, never in `config.json`. Personal tools always require approval. Users can choose **Bearer token / personal access token** under Authentication. For GitHub,
use `https://api.githubcopilot.com/mcp/` and paste the PAT without the `Bearer ` prefix. Tokens are
sent separately to `POST /connections/{id}/credentials`, stored as plaintext JSON unless host protection hooks are configured,
and never written to configuration files or returned to the UI. The config uses `auth.mode: "bearer"`.
Leave the field blank when editing to retain the token; enter a replacement to rotate it. Connection
changes may require re-entry. **Clear saved credentials** removes it after confirmation; Disable retains it. Use **Tools** on a connected row to search tools, select or deselect them for your conversation, or select all matching search results at once. If a saved personal tool filter hides everything, **Show discovered tools** opens a reviewable settings change; saving it may require re-authentication and Bearer connections must re-enter their token.

For GitHub OAuth, first register a GitHub OAuth App with the callback URL shown in the connection
form. Use `https://api.githubcopilot.com/mcp/` as the server URL and
`https://github.com/login/oauth` as the issuer. Enter the App's client ID and secret, then
complete browser sign-in. The callback notifies the open Tools page to load the connection's tools
and attempts to close its tab; **Close this tab** and the Tools page's **Load tools** action remain
available if the browser does not finish those steps automatically. llms-py derives the MCP
callback from a configured GitHub sign-in URL or the current local loopback address. For other
remote deployments, set `mcp_client.oauthRedirectUri` explicitly. Local single-user installations
can use GitHub OAuth over loopback HTTP without configuring a callback URL separately.

Users cannot set host-secret references,
audience rules, approval bypasses, or network exceptions. Shared connections are read-only in the UI;
the authenticated `default` account cannot edit the shared file through the personal API.

Shared and personal IDs must be distinct. Files are read on subsequent requests; no restart is
needed for connection edits. Saves use atomic replacement and revision checks. Edits invalidate
old tool handles and OAuth callbacks and require new sign-in when credentials no longer match.
Personal identity is included in the configuration hash so shared credentials cannot be inherited.
Files are limited to 64 KiB and 64 connections. Share this directory across instances, alongside
the database. OAuth tokens remain outside connection configuration JSON.

For shared service credentials, configure `auth: {"mode": "host_secret", "secretReference":
"KNOWLEDGE_MCP_TOKEN"}` and an explicit `allowedUsers` audience in the default file. The reference
names a server environment variable or host credential-store entry. Shared definitions may also
set `requiredRoles`, `approval`, `toolsWithoutApproval`, and `revision`.
As in C#, an omitted or empty `allowedUsers` list imposes no username filter; a nonempty
list matches exact usernames (`"*"` is not a wildcard). Host-secret connections require
an explicit audience or a host authorization hook.

Empty `allowedTools` exposes no tools; `"*"` explicitly allows all; `deniedTools` takes precedence.
`all` includes local tools unless `includeInAll` is true. Select `mcp_knowledge` or the alias returned
by discovery. Host identity and role checks still apply to remotely anonymous connections.

## OAuth and embedding hooks

OAuth requires host authentication or single-user mode, a registered client, an HTTPS callback
(or local HTTP loopback callback), and expected issuer.
Standalone llms-py stores tokens, client secrets, and pending PKCE state as plaintext JSON in its
owner-scoped MCP SQLite database. Keep the database private to the host account and share it if
running multiple instances. Bearer-token connections also work in single-user mode without host
authentication.

Before installing extensions, an embedding host may set `app.mcp_client_hooks`:

```python
app.mcp_client_hooks = {
    # Optional purpose-bound protection; otherwise payloads are stored as plaintext JSON.
    # protect(plaintext_json: str, purpose: str) -> encrypted_string
    # unprotect(encrypted_string: str, purpose: str) -> plaintext_json
    "protect": vault.protect,
    "unprotect": vault.unprotect,
    # Optional: sync or async. Revision must change when credentials rotate.
    "credentialStore": resolve_host_credential,  # reference -> {accessToken, revision}
    "authorize": authorize_connection,  # (server_config, context, operation) -> bool
    "reauthorizeBackgroundRequest": current_request_for_user,  # username -> request or None
}
```

If both `protect` and `unprotect` hooks are omitted, the payload is stored unchanged. Configure
both hooks to use a host vault. Supplying only one hook is rejected. The test suite exercises both
the plaintext default and the host hook contract.

The OAuth flow uses PKCE S256, protected-resource metadata, exact issuer/resource binding, one-time
callbacks, refresh tokens and database credential leases. Metadata follows a `WWW-Authenticate` protected-resource location when supplied, otherwise uses
well-known discovery, with origin-level and OpenID discovery fallbacks. Dynamic client registration
is not implemented. Authorization transactions and
host protection hooks receive a purpose that includes the user and server.
Only one sign-in per account and connection may be pending at a time, with a 128-flow cap and a
five-minute expiry. The callback state is hashed in storage, callback inputs are bounded, and a
changed connection or redirect is rejected before token exchange. The callback reports success only
after tool discovery completes; it waits up to 60 seconds for that work. Its registered HTTPS URL is
an inbound browser destination and is not constrained by the outbound MCP port allow-list.
Disconnect disables the binding and retains tokens. Delete credentials removes local tokens but
keeps a saved OAuth App client secret for another sign-in. Neither
operation revokes access at the remote identity provider. Embedding hosts can call
`app.mcp_client.delete_user_credentials(username)` when deleting an account to remove
its MCP credentials, pending authorizations, approvals, and cached connection state.

The Python callback transaction persists its PKCE state in the MCP database, so it can survive a process restart;
the current C# SDK keeps that exchange in memory. This is a backend-specific lifecycle difference, with
the same endpoints and UI behavior.

## Durable execution

Approval rows and invocation claims are stored in
`~/.llms/user/default/mcp_client/mcp.sqlite`, partitioned by owner. Canonical conversations continue to
use the app database. An entire mixed local/remote batch pauses before pending approvals execute;
resume restores provider call IDs and appends tool results without replacing canonical history.

The agent run enters `waiting_approval` and releases its scheduler lease. Every invocation is claimed
before dispatch. A lost response becomes `outcome_unknown`, blocks continuation, and is never
transparently retried. The form offers **I have checked; continue without replay** after the user has
reconciled the remote outcome. An abandoned executing approval is classified after ten minutes,
longer than the maximum request and cleanup deadlines. Local bookkeeping cannot guarantee exactly-once
remote effects. A remote confirmation-token response remains ordinary output and is never redeemed
automatically by a local approval.

Live runs recheck the current authenticated request. Runs resumed after restart require either a
current approval/continue request or `reauthorizeBackgroundRequest`. That hook must check current
account, tenant and role state; a stored username alone is not authorization. There is no SDK client
or bearer token in the agent's persisted context. A direct `/v1/chat/completions` request that pauses
returns HTTP 202 with `threadId` and `status: "Approval required"` and continues through the durable
scheduler after a decision.

## HTTP contract

Routes are relative to `/ext/mcp_client` (C# prefixes these with its configured `/chat` base):

| Method | Path | Result |
| --- | --- | --- |
| GET | `/connections` | User-visible connection status array |
| GET | `/connections/{id}/tools` | Tool aliases, remote names, schemas, approval policy and group |
| POST | `/connections/{id}/refresh` | Refreshed connection status |
| POST | `/connections/{id}/connect` | `{ "authorizationUrl": null or URL }` |
| POST | `/connections/{id}/disconnect` | `{ "state": "disconnected" }` |
| DELETE | `/connections/{id}/credentials` | `{ "state": "disconnected" }` |
| GET | `/oauth/callback` | Sign-in completion HTML |
| GET | `/approvals/{threadId}` | Owned approval rows |
| POST | `/approvals/{id}/approve` | `{ "args": { ... } }` → updated approval |
| POST | `/approvals/{id}/reject` | `{ "reason": "..." }` → updated approval |
| POST | `/approvals/{id}/reconcile` | `{ "decision": "continue_without_replay" }` → updated approval |
| GET / POST | `/config.json` | Read or save personal servers with revision checking |
| POST | `/approval-batches/{id}/continue` | Requeue a resolved batch without replaying calls |

Mutation routes require `X-Mcp-Client: 1`, authenticated ownership and a non-cross-site request.
Errors expose `responseStatus.errorCode` and `responseStatus.message`, matching the shared UI.
`/ext/tools` merges the current user's catalog without modifying the global local-tool registry.
`/ext/tools/exec/{name}` cannot bypass MCP approvals, selection or authorization.

## Protocol, limits, and intentional backend differences

- Streamable HTTP with JSON and finite SSE responses; no standalone notification streams or legacy
  SSE transport. The client probes `server/discover` for the per-request `2026-07-28` protocol and falls
  back to initialization-based revisions on an unsupported discovery method. Set `protocolVersion`
  to pin a supported generation. Fixtures cover `2025-06-18` and `2026-07-28`; older negotiated revisions
  are accepted but not individually certified.
- Sessions are operation-scoped, catalogs cached for five minutes. Discovery defaults to 15 seconds,
  calls to 60 seconds. Both include queue wait. Default limits match C#: 256 tools, 100 pages, 64 KiB
  schemas/arguments, 1 MiB catalogs/selected definitions, 4 MiB responses, 128 principal partitions,
  4 active calls per principal, 16 per server, 32 queued calls and 15-minute idle eviction. Configure
  them with camelCase keys under `limits`; duration values are seconds. Quotas are per process.
- HTTPS/443, standard TLS checks, DNS address checking at connection time, no ambient proxy/cookies,
  redirects, remote resource fetching or trace-header propagation. Development fixtures explicitly use
  `networkPolicy.allowHttp`, `allowedPorts` and `allowedPrivateNetworks` (CIDR strings).
- Input/output schemas support nested objects/arrays, local nonrecursive JSON Pointers, nullable types,
  enums, numeric/string/array constraints, combinators and conditional/dependent schemas. Unsupported
  keywords fail closed. Unlike the C# JsonSchema.Net dependency, this bounded stdlib implementation does
  not implement the entire JSON Schema vocabulary. Regex/dynamic/remote references, unevaluated
  constraints and MCP header-mapping schemas are rejected. `format` is an annotation. Google selections
  reject schemas whose `additionalProperties` assertion the existing adapter would remove.
- Text, structured JSON including arrays/scalars, references, and supported raster/audio data retain
  provenance. Private media stays in the owned result as bounded data URLs, never in the public cache.
  MIME signatures are checked; unsupported content is reported explicitly.
- Stdio, prompts/resources browsing, sampling, roots, elicitation, MCP Apps, MCP Tasks and multi-round
  tool continuation are deferred, matching the current C# release scope. Continuation responses and
  repeated tool dispatch within one session fail visibly without replay.

## Validation

```sh
.venv/bin/python -m unittest tests.test_mcp_client
node tests/test_mcp_ui_contract.mjs
```

Tests use temporary SQLite files and loopback servers, with no remote accounts or model charges.
They cover both transport generations, SSE, aliases, policy/credential changes, cross-user access,
PKCE exchange/refresh/callback replay, edited/duplicate approvals, mixed batches, uncertain outcomes,
and a real scheduler pause/approve/resume preserving canonical history. The UI fixture exercises the
same module's prefixed URLs, tool selection, approval edits and reconciliation. Live third-party OAuth
interoperability and load certification remain deployment validation work.

**Refresh connections** reloads status. **Connect all** connects all enabled connections with saved credentials and reports failures individually. OAuth requiring browser sign-in must be connected individually. **Connection options → Disable** persists the user’s choice and excludes that connector from bulk connections and tool discovery. Credentials are retained; **Enable & connect** restores the connector.
