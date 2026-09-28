"""Outbound MCP client. HTTP routes and UI match ServiceStack.AI.Chat."""

import asyncio
import base64
import logging
import re
from html import escape
import secrets

from aiohttp import web

from .approvals import Approvals
from .client import Client
from .common import McpError, config_hash


logger = logging.getLogger(__name__)


def oauth_callback_page(success, error_code=None, server_id=None, phase=None):
    title = (
        "Connection authorized" if success else
        "Signed in, but tools could not load" if phase == "tools" else
        "Connection could not be completed"
    )
    safe_code = error_code if isinstance(error_code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) else "connection_error"
    detail = (
        "Returning to the Tools page. If this tab stays open, close it to continue."
        if success else
        f"The OAuth token was saved, but the MCP server did not load its tools ({safe_code}). Return to Tools and try Refresh tools."
        if phase == "tools" else
        f"The OAuth provider did not issue a usable token ({safe_code}). Check your OAuth App credentials and try signing in again."
        if phase == "token" else
        f"Sign-in could not finish ({safe_code}). Return to Tools and try again."
    )
    mark = "✓" if success else "!"
    nonce = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · llms-py</title><style>
:root{color-scheme:light dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;font:15px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;color:#172033}
main{width:min(100%,430px);padding:32px;border:1px solid #e2e8f0;border-radius:16px;background:#fff;box-shadow:0 14px 40px #0f172a0d}.mark{display:grid;place-items:center;width:42px;height:42px;border-radius:12px;background:__MARK_BACKGROUND__;color:__MARK_COLOR__;font-size:24px;font-weight:700}h1{margin:20px 0 8px;font-size:21px;line-height:1.3}p{margin:0;color:#5f6b7a}@media(prefers-color-scheme:dark){body{background:#0f172a;color:#f1f5f9}main{background:#172033;border-color:#344155;box-shadow:none}p{color:#aeb8c6}}
button{margin-top:24px;padding:10px 15px;border:1px solid #cbd5e1;border-radius:8px;background:#fff;color:#172033;font:600 14px system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;cursor:pointer}button:hover{background:#f1f5f9}button:focus-visible{outline:2px solid #2563eb;outline-offset:2px}@media(prefers-color-scheme:dark){button{background:#243248;color:#f1f5f9;border-color:#516078}button:hover{background:#30415c}}
</style></head><body data-server-id="__SERVER_ID__" data-error-code="__ERROR_CODE__" data-error-phase="__ERROR_PHASE__"><main><div class="mark" aria-hidden="true">__MARK__</div><h1>__TITLE__</h1><p>__DETAIL__</p><button id="close-tab" type="button">Close this tab</button></main>
<script nonce="__NONCE__">(() => {
  const close = () => window.close();
  document.getElementById('close-tab').addEventListener('click', close);
  try {
    const channel = new BroadcastChannel('ai-chat-mcp-oauth');
    channel.postMessage(document.body.dataset.serverId
      ? { type: 'authorized', serverId: document.body.dataset.serverId }
      : { type: 'failed', errorCode: document.body.dataset.errorCode, phase: document.body.dataset.errorPhase });
    setTimeout(() => channel.close(), 1000);
  } catch (_) { /* The close button remains available. */ }
  if (document.body.dataset.serverId) setTimeout(close, 500);
})();</script></body></html>'''
    page = (page.replace("__TITLE__", title).replace("__DETAIL__", detail)
            .replace("__MARK__", mark)
            .replace("__MARK_BACKGROUND__", "#eaf8ef" if success else "#fff2e9")
            .replace("__MARK_COLOR__", "#16803d" if success else "#b45309")
            .replace("__SERVER_ID__", escape(server_id or "", quote=True) if success else "")
            .replace("__ERROR_CODE__", "" if success else safe_code)
            .replace("__ERROR_PHASE__", escape(phase or "", quote=True))
            .replace("__NONCE__", nonce))
    return web.Response(
        text=page, content_type="text/html", status=200 if success else 400,
        headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


def install(ctx):
    # A host override is authoritative. The regular llms app can configure this
    # extension in llms.json; without authentication its default account is the
    # single user of the MCP client.
    config = getattr(getattr(ctx, "app", None), "mcp_client_config", None)
    if config is None:
        config = (getattr(ctx, "config", None) or {}).get("mcp_client")
    config = {
        "enabled": True,
        "singleUserMode": not getattr(ctx, "is_auth_enabled", lambda: False)(),
        **(config or {}),
    }
    if not config.get("enabled", False):
        ctx.disabled = True
        return
    hooks = dict(getattr(ctx.app, "mcp_client_hooks", None) or {})
    client = Client(ctx, config, hooks)
    client.approvals = Approvals(client)
    ctx.app.mcp_client = client
    ctx.app.contextual_tool_providers.append(client)
    ctx.register_cleanup_handler(client.close)
    ctx.register_startup_handler(client.approvals.recover)

    def route(handler):
        async def wrapped(request):
            try:
                if ctx.is_auth_enabled() and not ctx.check_auth(request)[0]:
                    raise McpError("access_denied", "Authentication required")
                context = client.request_context(request)
                return await handler(request, context)
            except McpError as exc:
                status = (
                    404
                    if exc.code == "not_found"
                    else 403
                    if exc.code == "access_denied"
                    else 409
                    if exc.code in ("stale_tool", "outcome_unknown", "busy")
                    else 400
                )
                return web.json_response(
                    {"responseStatus": {"errorCode": exc.code, "message": str(exc)}}, status=status
                )
            except Exception:
                # Host vault and HTTP exceptions can contain endpoint or credential
                # details. Keep the public error contract sanitized.
                return web.json_response(
                    {"responseStatus": {"errorCode": "connection_error", "message": "MCP request failed"}}, status=502
                )

        return wrapped

    async def oauth_issuers(request, context):
        client.mutation(request)
        body = await request.json()
        endpoint = body.get("endpoint") if isinstance(body, dict) else None
        if not isinstance(endpoint, str) or len(endpoint) > 2048:
            raise McpError("invalid_arguments", "Enter a valid MCP server URL")
        client.policy.uri(endpoint)
        return web.json_response({"issuers": await client.oauth.discover_issuers(endpoint)})

    async def connections(request, context):
        rows = []
        for server in client.get_servers(context["owner"]):
            try:
                await client.access(server, context, "discover")
            except McpError:
                continue
            rows.append({**client.status(context["owner"], server), "scope": "personal" if server.get("_scope") else "shared"})
        return web.json_response(rows)

    async def tools(request, context):
        server = client.server(request.match_info["id"], context["owner"])
        catalog = await client.catalog(server, context)
        return web.json_response(
            [
                {
                    **{k: (client.needs_approval(server, t, context["owner"]) if k == "requiresApproval" else v)
                       for k, v in t.items()
                       if k in ("name", "remoteName", "description", "schema", "requiresApproval", "group")},
                    "alwaysApproved": client.store.has_approval_grant(
                        context["owner"], server["id"], t["remoteName"], config_hash(server), t["schemaHash"]
                    ),
                }
                for t in catalog
            ]
        )

    async def approval_grants(request, context):
        server = client.server(request.match_info["id"], context["owner"])
        await client.access(server, context, "discover")
        return web.json_response(client.store.approval_grants(context["owner"], server["id"], config_hash(server)))

    async def revoke_approval_grant(request, context):
        client.mutation(request)
        server = client.server(request.match_info["id"], context["owner"])
        await client.access(server, context, "connect")
        body = await request.json()
        tool = body.get("tool") if isinstance(body, dict) else None
        if not isinstance(tool, str) or not tool.strip():
            raise McpError("invalid_arguments", "Tool name required")
        client.store.revoke_approval_grant(context["owner"], server["id"], tool)
        return web.json_response({"revoked": True})

    async def refresh(request, context):
        client.mutation(request)
        server = client.server(request.match_info["id"], context["owner"])
        await client.catalog(server, context, refresh=True)
        return web.json_response(client.status(context["owner"], server))

    async def connect_all(request, context):
        client.mutation(request)
        results = []
        for server in client.get_servers(context["owner"]):
            try:
                await client.access(server, context, "connect")
            except McpError:
                continue
            if client.store.binding(context["owner"], server["id"])["disconnected"]:
                continue
            result = {"id": server["id"], "name": server.get("displayName") or server["id"]}
            try:
                # Reuse saved credentials without enabling bindings or opening OAuth sign-in.
                await client.catalog(server, context, refresh=True)
                result["connected"] = True
            except Exception as exc:
                result.update(connected=False, error=str(exc) if isinstance(exc, McpError) else "MCP connection failed")
            results.append(result)
        return web.json_response(results)

    async def connect(request, context):
        client.mutation(request)
        server = client.server(request.match_info["id"], context["owner"])
        await client.access(server, context, "connect")
        if server["auth"]["mode"] == "user_oauth" and client.store.oauth_pending(context["owner"], server["id"]):
            raise McpError("busy", "Sign-in is already in progress")
        client.store.connect(context["owner"], server["id"])
        client.invalidate(context["owner"], server["id"])
        url = None
        if server["auth"]["mode"] == "user_oauth":
            url = await client.oauth.start(server, context)
        else:
            await client.catalog(server, context, refresh=True)
        return web.json_response({"authorizationUrl": url})

    async def disconnect(request, context):
        client.mutation(request)
        server = client.server(request.match_info["id"], context["owner"])
        await client.access(server, context, "connect")
        client.store.connect(
            context["owner"], server["id"], disconnected=True, delete=request.method == "DELETE",
            preserve_client_secret=request.method == "DELETE" and server["auth"]["mode"] == "user_oauth",
        )
        client.invalidate(context["owner"], server["id"])
        return web.json_response({"state": "disconnected"})

    async def save_credentials(request, context):
        client.mutation(request)
        server = client.server(request.match_info["id"], context["owner"])
        await client.access(server, context, "connect")
        body = await request.json()
        if not isinstance(body, dict):
            raise McpError("invalid_arguments")
        client.save_bearer(server, context["owner"], body.get("token"))
        return web.json_response({"saved": True})

    async def save_oauth_client_secret(request, context):
        client.mutation(request)
        server = client.server(request.match_info["id"], context["owner"])
        await client.access(server, context, "connect")
        body = await request.json()
        if not isinstance(body, dict):
            raise McpError("invalid_arguments")
        client.save_oauth_client_secret(server, context["owner"], body.get("secret"))
        return web.json_response({"saved": True})

    async def callback(request, context):
        try:
            async with asyncio.timeout(60):
                server_id = await client.oauth.complete(request, context)
        except TimeoutError:
            logger.warning("MCP OAuth token exchange timed out")
            return oauth_callback_page(False, "oauth_timeout", phase="token")
        except McpError as exc:
            logger.warning("MCP OAuth token exchange failed: %s", exc.code)
            return oauth_callback_page(False, exc.code, phase="token")
        try:
            async with asyncio.timeout(60):
                await client.catalog(client.server(server_id, context["owner"]), context, refresh=True)
        except TimeoutError:
            logger.warning("MCP OAuth tool discovery timed out")
            return oauth_callback_page(False, "oauth_timeout", phase="tools")
        except McpError as exc:
            logger.warning("MCP OAuth tool discovery failed: %s", exc.code)
            return oauth_callback_page(False, exc.code, phase="tools")
        return oauth_callback_page(True, server_id=server_id)

    async def approvals(request, context):
        thread_id = request.match_info["threadId"]
        client.approvals.own_thread(context, thread_id)
        rows = client.store.rows(context["owner"], thread=thread_id)
        return web.json_response(rows)

    async def decide(request, context):
        client.mutation(request)
        try:
            body = await request.json()
        except ValueError:
            raise McpError("invalid_arguments") from None
        if not isinstance(body, dict):
            raise McpError("invalid_arguments")
        result = await client.approvals.decide(request.match_info["id"], request.match_info["action"], body, context)
        return web.json_response(result)

    async def continue_batch(request, context):
        client.mutation(request)
        batch = client.store.get_batch(request.match_info["id"], context["owner"])
        if not batch:
            raise McpError("not_found")
        client.approvals.own_thread(context, batch["threadId"])
        if batch["runId"]:
            ctx.app.agent_requests[batch["runId"]] = request
        await client.approvals.wake_ready(context["owner"], batch["threadId"])
        rows = client.store.rows(context["owner"], batch=batch["id"])
        return web.json_response(
            {
                "id": batch["id"],
                "threadId": int(batch["threadId"]),
                "status": "completed" if batch["status"] == "completed" else "pending",
                "createdAt": rows[0]["createdAt"] if rows else None,
                "updatedAt": rows[-1]["updatedAt"] if rows else None,
                "completedAt": None,
            }
        )

    async def configuration(request, context):
        if request.method == "POST":
            client.mutation(request)
            raw = bytearray()
            async for chunk in request.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise McpError("invalid_configuration")
            try:
                import json
                body = json.loads(raw)
            except ValueError:
                raise McpError("invalid_configuration") from None
            saved = client.save_configuration(context["owner"], body)
            return web.json_response({**saved, "oauthRedirectUri": client.oauth_redirect_uri(request)})
        saved = client.personal_configuration(context["owner"])
        return web.json_response({**saved, "oauthRedirectUri": client.oauth_redirect_uri(request)})

    ctx.add_get("config.json", route(configuration))
    ctx.add_post("config.json", route(configuration))
    ctx.add_post("oauth/issuers", route(oauth_issuers))
    ctx.add_get("connections", route(connections))
    ctx.add_get("connections/{id}/tools", route(tools))
    ctx.add_get("connections/{id}/approval-grants", route(approval_grants))
    ctx.add_post("connections/{id}/approval-grants/revoke", route(revoke_approval_grant))
    ctx.add_post("connections/{id}/refresh", route(refresh))
    ctx.add_post("connections/connect-all", route(connect_all))
    ctx.add_post("connections/{id}/connect", route(connect))
    ctx.add_post("connections/{id}/disconnect", route(disconnect))
    ctx.add_post("connections/{id}/credentials", route(save_credentials))
    ctx.add_post("connections/{id}/oauth-client-secret", route(save_oauth_client_secret))
    ctx.add_delete("connections/{id}/credentials", route(disconnect))
    ctx.add_get("oauth/callback", route(callback))
    ctx.add_get("approvals/{threadId}", route(approvals))
    ctx.add_post("approvals/{id}/{action:approve|reject|reconcile}", route(decide))
    ctx.add_post("approval-batches/{id}/continue", route(continue_batch))


__install__ = install
