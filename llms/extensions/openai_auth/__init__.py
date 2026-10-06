"""Connect a per-user ChatGPT grant using the public Sign in with ChatGPT flow."""

import asyncio
import contextlib
import copy
import sys
import time
import urllib.parse

import aiohttp
from aiohttp import web

from llms.extensions.openai_auth.callback import CallbackReceiver
from llms.extensions.openai_auth.security import (
    Auth,
    Options,
    Store,
    SubscriptionError,
    endpoint,
    parse_object,
    read_file,
)
from llms.main import OpenAiCompatible, StreamCheckpointWriter, get_client_timeout, should_cancel_thread

_auth_instances = []


def _get_main_mod():
    return sys.modules.get("llms.main")


def _get_handlers(ctx=None):
    return getattr(_get_main_mod(), "g_handlers", {})


def _set_handler(ctx, name, provider):
    ctx.register_provider_handler(name, provider)
    _get_handlers(ctx)[name] = provider


def _auth(ctx):
    value = getattr(ctx, "openai_subscription_auth", None)
    if not isinstance(value, Auth):
        options = getattr(ctx, "openai_subscription_options", None)
        value = Auth(ctx, options if isinstance(options, Options) else None)
        ctx.openai_subscription_auth = value
        _auth_instances.append(value)
    return value


def _get_creds_file(ctx, user=None):
    return Store(ctx).path(user)


def _load_creds(ctx, user=None):
    return Store(ctx).load(user) or {}


def _save_creds(ctx, creds, user=None):
    Store(ctx).save(user, creds)


async def get_valid_access_token(ctx, user=None):
    credentials = await _auth(ctx).valid_credentials(user)
    return credentials.get("access_token") if credentials else None


class OpenAiSubscriptionProvider(OpenAiCompatible):
    sdk = "@ai-sdk/openai"

    def __init__(self, ctx, base_provider=None, **kwargs):
        self.ctx, self.base_provider, self.auth = ctx, base_provider, _auth(ctx)
        mod = _get_main_mod()
        definition = copy.deepcopy((getattr(mod, "g_providers", None) or {}).get("openai", {}))
        definition.update(copy.deepcopy((getattr(mod, "g_config", None) or {}).get("providers", {}).get("openai", {})))
        definition.update(kwargs)
        definition.update({"id": "openai", "name": "OpenAI", "api": "https://api.openai.com/v1", "api_key": None})
        super().__init__(**definition)
        if base_provider:
            self.models = copy.deepcopy(base_provider.models)
            self.map_models = copy.deepcopy(getattr(base_provider, "map_models", {}))
            self.modalities = dict(getattr(base_provider, "modalities", {}))
        self.headers = {}  # No user's bearer credentials live on a shared object.

    def validate(self, **kwargs):
        return None

    def test(self, **kwargs):
        return True

    def provider_model(self, model):
        # Qualification has no user context. Actual request resolution uses only
        # the current user's account catalog below; names never become outbound IDs.
        candidate = model.removeprefix("openai/")
        for _, _, rows in self.auth.catalogs.values():
            for row in rows:
                if candidate.lower() in (row["id"].lower(), row["name"].lower()):
                    return row["id"]
        return super().provider_model(model) or (candidate if model.startswith("openai/") else None)

    def model_info(self, model):
        candidate = self.provider_model(model) or model
        for _, _, rows in self.auth.catalogs.values():
            for row in rows:
                if row["id"] == candidate:
                    return copy.deepcopy(row)
        return super().model_info(model) or {}

    def _convert_messages_to_input(self, messages: list) -> list:
        input_items = []
        for msg in messages:
            role = msg.get("role")
            if role in ("system", "developer"):
                input_items.append({"role": "system", "content": msg.get("content", "")})
            elif role == "user":
                raw_content = msg.get("content")
                if isinstance(raw_content, str):
                    input_items.append({"role": "user", "content": raw_content})
                elif isinstance(raw_content, list):
                    parts = []
                    for part in raw_content:
                        if isinstance(part, str):
                            parts.append({"type": "input_text", "text": part})
                        elif isinstance(part, dict):
                            p_type = part.get("type")
                            if p_type == "text":
                                parts.append({"type": "input_text", "text": part.get("text", "")})
                            elif p_type == "image_url":
                                url = (
                                    part.get("image_url", {}).get("url")
                                    if isinstance(part.get("image_url"), dict)
                                    else part.get("image_url")
                                )
                                parts.append({"type": "input_image", "image_url": url})
                            elif p_type in ("input_text", "input_image", "input_audio", "input_file"):
                                parts.append(copy.deepcopy(part))
                            else:
                                parts.append({"type": "input_text", "text": part.get("text") or str(part)})
                    input_items.append({"role": "user", "content": parts})
                else:
                    input_items.append({"role": "user", "content": str(raw_content or "")})
            elif role == "assistant":
                content = msg.get("content")
                tool_calls = msg.get("tool_calls")
                if content:
                    input_items.append({"role": "assistant", "content": content})
                if tool_calls and isinstance(tool_calls, list):
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        input_items.append(
                            {
                                "type": "function_call",
                                "call_id": tc.get("id") or tc.get("call_id") or "",
                                "name": fn.get("name") or tc.get("name") or "",
                                "arguments": fn.get("arguments") or tc.get("arguments") or "{}",
                            }
                        )
            elif role in ("tool", "function"):
                call_id = msg.get("tool_call_id") or msg.get("call_id") or msg.get("name") or ""
                output = msg.get("content") or ""
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": str(output),
                    }
                )
        return input_items

    def _convert_tools(self, tools: list) -> list:
        codex_tools = []
        for t in tools:
            if t.get("type") == "function":
                fn = t.get("function") or {}
                codex_tools.append(
                    {
                        "type": "function",
                        "name": fn.get("name") or t.get("name"),
                        "description": fn.get("description") or t.get("description", ""),
                        "parameters": fn.get("parameters") or t.get("parameters", {}),
                    }
                )
                if "strict" in fn:
                    codex_tools[-1]["strict"] = fn["strict"]
            else:
                codex_tools.append(copy.deepcopy(t))
        return codex_tools

    async def chat(self, chat, context=None):
        context = context or {}
        user = context.get("user") or "default"
        # File presence, including invalid legacy grants, prevents paid fallback.
        if self.auth.store.load(user) is None:
            if self.base_provider and getattr(self.base_provider, "api_key", None):
                return await self.base_provider.chat(copy.deepcopy(chat), context=context)
            raise SubscriptionError("ChatGPT subscription is not connected. Sign in in Settings.")
        if any(m != "text" for m in chat.get("modalities", [])):
            raise SubscriptionError(
                "ChatGPT subscription supports text responses only. The OpenAI API key is disabled while connected. "
                "Disconnect the subscription in Settings to use the OpenAI API key for image or audio generation."
            )
        try:
            grant = await self.auth.valid_credentials(user)
            if not grant or not grant.get("plan_enabled"):
                raise SubscriptionError("Authorize ChatGPT plan usage in Settings before sending a request.")
            rows = await self.auth.models(user)
            requested = (chat.get("model") or "").removeprefix("openai/")
            model = next((r["id"] for r in rows if requested.lower() in (r["id"].lower(), r["name"].lower())), None)
            if model is None:
                mapped = super().provider_model(requested)
                model = next((r["id"] for r in rows if r["id"] == mapped), None)
            if model is None:
                raise SubscriptionError(
                    "The selected model is unavailable for this ChatGPT account. Select a model from its catalog."
                )
            # Media normalization operates on a copy, never canonical history.
            normalized = await self.process_chat(copy.deepcopy(chat), provider_id="openai")
            payload = {
                "model": model,
                "input": self._convert_messages_to_input(normalized.get("messages", [])),
                "stream": True,
                "store": False,
            }
            tools = self._convert_tools(normalized.get("tools", []))
            if tools:
                payload["tools"] = tools
            choice = normalized.get("tool_choice")
            if choice:
                payload["tool_choice"] = (
                    {"type": "function", "name": choice["function"]["name"]}
                    if isinstance(choice, dict) and "function" in choice
                    else choice
                )
            effort = normalized.get("reasoning_effort") or self.reasoning_effort
            if effort:
                payload["reasoning"] = {"effort": effort}
            if normalized.get("instructions"):
                payload["instructions"] = normalized["instructions"]
            started = time.time()
            writer = StreamCheckpointWriter(None, None) if context.get("nostore") else self.stream_writer(context)
            async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False) as session:
                for attempt in range(2):
                    if should_cancel_thread(context):
                        raise asyncio.CancelledError()
                    current = self.auth.store.load(user)
                    if current is None or (current.get("subject"), current.get("client_id")) != (
                        grant["subject"],
                        grant["client_id"],
                    ):
                        raise SubscriptionError("The subscription changed before sending the response request.")
                    headers = {
                        "Authorization": "Bearer " + grant["access_token"],
                        "Accept": "text/event-stream",
                        "User-Agent": "llms-py/4",
                    }
                    async with session.post(
                        endpoint(self.auth.options.responses_url),
                        headers=headers,
                        json=payload,
                        timeout=get_client_timeout(streaming=True),
                        allow_redirects=False,
                    ) as response:
                        if response.status == 401 and attempt == 0:
                            grant = await self.auth.valid_credentials(user, rejected_token=grant["access_token"])
                            if not grant or not grant.get("plan_enabled"):
                                raise SubscriptionError("The ChatGPT subscription is no longer authorized.")
                            continue
                        return await self._process_responses_stream(response, chat, started, context, writer, grant)
        except SubscriptionError:
            raise
        except Exception:
            raise SubscriptionError(
                "ChatGPT subscription request failed. It has not been retried. Check your account or sign in again."
            ) from None
        raise SubscriptionError("ChatGPT subscription credentials were rejected after refresh. Sign in again.")

    async def _process_responses_stream(self, response, chat, started, context, writer, grant=None):
        if response.status != 200:
            raise SubscriptionError(
                f"ChatGPT subscription request failed (HTTP {response.status}). Check your account or sign in again."
            )
        content, reasoning, calls, usage = "", "", {}, {}
        response_id, model, created, complete = None, None, None, False
        total = 0
        frame = []
        frame_bytes = 0

        def message(partial=False):
            value = {"role": "assistant", "content": content if content or not calls else None}
            if partial:
                value.update({"model": model or chat.get("model"), "timestamp": int(started * 1000)})
            if reasoning:
                value["reasoning_content"] = reasoning
            if calls:
                value["tool_calls"] = copy.deepcopy([calls[i] for i in sorted(calls)])
            return value

        def assert_current():
            if should_cancel_thread(context):
                raise asyncio.CancelledError()
            if self.auth.closed:
                raise SubscriptionError("Subscription service is shutting down.")
            if grant:
                current = self.auth.store.load(context.get("user") or "default")
                if current is None or (current.get("subject"), current.get("client_id")) != (
                    grant["subject"],
                    grant["client_id"],
                ):
                    raise SubscriptionError("The subscription changed during the response.")

        async def event(data):
            nonlocal content, reasoning, usage, response_id, model, created, complete
            if not data or data == "[DONE]":
                return
            try:
                item = parse_object(data)
            except ValueError:
                raise SubscriptionError("ChatGPT returned an unreadable stream event.") from None
            kind = item.get("type")
            detail = item.get("response") or {}
            if kind == "response.created":
                response_id, model, created = detail.get("id"), detail.get("model"), detail.get("created_at")
            elif kind in ("response.output_text.delta", "response.refusal.delta"):
                content += item.get("delta", "")
            elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                reasoning += item.get("delta", "")
            elif kind == "response.output_item.added" and item.get("item", {}).get("type") == "function_call":
                call = item["item"]
                calls[item.get("output_index", 0)] = {
                    "id": call.get("call_id", ""),
                    "type": "function",
                    "function": {"name": call.get("name", ""), "arguments": call.get("arguments", "")},
                }
            elif kind in ("response.function_call_arguments.delta", "response.function_call_arguments.done"):
                call = calls.get(item.get("output_index", 0))
                if call:
                    fn = call["function"]
                    fn["arguments"] = (
                        item.get("arguments", fn["arguments"])
                        if kind.endswith(".done")
                        else fn["arguments"] + item.get("delta", "")
                    )
            elif kind == "response.completed":
                if detail.get("status", "completed") != "completed":
                    raise SubscriptionError("ChatGPT did not complete the response.")
                complete, usage = True, detail.get("usage") or {}
                response_id, model = response_id or detail.get("id"), model or detail.get("model")
            elif kind in ("response.failed", "response.incomplete", "error"):
                raise SubscriptionError(
                    "ChatGPT did not complete the response. Review your account limits or sign in again."
                )
            assert_current()
            await writer.write(message(True))

        try:
            # aiohttp's iterator yields lines; enforce a frame/total bound as well
            # as the finite socket-read timeout configured by get_client_timeout.
            async for raw in response.content:
                total += len(raw)
                if total > 32 * 1024 * 1024:
                    raise SubscriptionError("ChatGPT response exceeds the size limit.")
                assert_current()
                line = raw.decode("utf-8").rstrip("\r\n")
                if not line:
                    await event("\n".join(frame))
                    frame, frame_bytes = [], 0
                    if complete:
                        break
                elif line.startswith("data:"):
                    text = line[5:].lstrip(" ")
                    frame_bytes += len(raw)
                    if frame_bytes > 2 * 1024 * 1024:
                        raise SubscriptionError("ChatGPT stream event exceeds the size limit.")
                    frame.append(text)
            if frame and not complete:
                await event("\n".join(frame))
            if not complete:
                raise SubscriptionError("ChatGPT stream ended before completion. The request has not been retried.")
            assert_current()
            await writer.write(message(True), final=True)
        except BaseException:
            await writer.flush()
            raise
        input_tokens, output_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        result = {
            "id": response_id,
            "object": "chat.completion",
            "created": created or int(started),
            "model": model or chat.get("model"),
            "choices": [{"index": 0, "message": message(), "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": usage.get("total_tokens", input_tokens + output_tokens),
                "prompt_tokens_details": {
                    "cached_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
                },
                "completion_tokens_details": {
                    "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
                },
            },
            "cost": 0.0,
        }
        result = self.to_response(result, chat, started, context=context)
        result["metadata"]["pricing"] = "0/0"
        return result


async def cleanup_active_flow():
    for auth in _auth_instances:
        await auth.cleanup()
    _auth_instances.clear()


def activate_subscription_provider(ctx):
    current = _get_handlers(ctx).get("openai")
    if isinstance(current, OpenAiSubscriptionProvider):
        return
    _set_handler(ctx, "openai", OpenAiSubscriptionProvider(ctx, base_provider=current))


def deactivate_subscription_provider(ctx):
    current = _get_handlers(ctx).get("openai")
    if isinstance(current, OpenAiSubscriptionProvider):
        if current.base_provider:
            _set_handler(ctx, "openai", current.base_provider)
        else:
            _get_handlers(ctx).pop("openai", None)


def install(ctx):
    auth = _auth(ctx)
    ctx.register_shutdown_handler(auth.close)
    if callable(getattr(ctx, "register_cleanup_handler", None)):
        ctx.register_cleanup_handler(auth.cleanup)
    if auth.callback_receiver is None:
        auth.callback_receiver = CallbackReceiver(auth, lambda _: activate_subscription_provider(ctx))
    # The model selector remains unchanged. Its existing /models endpoint receives
    # an account-scoped catalog from this server-side adaptation.
    ctx.app.openai_subscription_auth = auth

    def user(request):
        return ctx.assert_username(request) or "default"

    async def guarded(request, action):
        try:
            origin = request.headers.get("Origin")
            if origin and origin != f"{request.scheme}://{request.host}":
                raise SubscriptionError("Subscription settings require a same-origin request.")
            return web.json_response(await action(user(request)), headers={"Cache-Control": "no-store"})
        except SubscriptionError as error:
            return web.json_response(
                ctx.create_error_response(str(error)), status=400, headers={"Cache-Control": "no-store"}
            )

    async def status(request):
        async def action(username):
            grant = auth.store.load(username)
            credentials = grant or {}
            account = credentials.get("account") or {}
            provider = _get_handlers(ctx).get("openai")
            base = provider.base_provider if isinstance(provider, OpenAiSubscriptionProvider) else provider
            has_api_key = bool(base and getattr(base, "api_key", None))
            connected = (
                bool(credentials.get("access_token"))
                and credentials.get("issuer") == auth.options.issuer
                and bool(credentials.get("subject"))
            )
            can_import = (
                callable(auth.options.local_credentials_path)
                and callable(auth.options.can_import_local_credentials)
                and auth.options.can_import_local_credentials(request)
            )
            return {
                "connected": connected,
                "email": account.get("email", ""),
                "name": account.get("name", ""),
                "plan": account.get("plan", ""),
                "account_id": credentials.get("account_id", ""),
                "expires_at": credentials.get("expires_at", 0),
                "expired": connected and time.time() >= credentials.get("expires_at", 0),
                "plan_enabled": bool(credentials.get("plan_enabled")),
                "has_api_key": has_api_key,
                "api_key_active": has_api_key and grant is None,
                "api_key_disabled": has_api_key and grant is not None,
                "has_codex_auth": bool(can_import),
                "pending": auth.has_pending(username),
                "manual_callback": not auth.options.automatic_callback,
                "automatic_callback": auth.options.automatic_callback,
                "callback_error": auth.callback_error(username),
                "requires_reconnect": bool(credentials) and not connected,
            }

        return await guarded(request, action)

    async def connect(request):
        async def action(username):
            body = await read_body(request)
            return_url = body.get("return_url")
            origin = f"{request.scheme}://{request.host}"
            if return_url is not None:
                try:
                    if (
                        not isinstance(return_url, str)
                        or len(return_url) > 8192
                        or any(ord(c) < 32 for c in return_url)
                    ):
                        raise ValueError()
                    target = urllib.parse.urlsplit(return_url)
                    if (
                        target.scheme not in ("http", "https")
                        or target.username is not None
                        or f"{target.scheme}://{target.netloc}" != origin
                    ):
                        raise ValueError()
                except ValueError:
                    raise SubscriptionError("The sign-in return URL must belong to this app.") from None
            redirect = await auth.callback_receiver.start() if auth.options.automatic_callback else None
            return auth.connect(
                username,
                redirect_uri=redirect,
                automatic=auth.options.automatic_callback,
                return_url=return_url or origin + "/",
            )

        return await guarded(request, action)

    async def read_body(request):
        raw = bytearray()
        async for chunk in request.content.iter_chunked(4096):
            raw.extend(chunk)
            if len(raw) > 32 * 1024:
                raise SubscriptionError("Provide a JSON object under 32 KB.")
        try:
            return parse_object(raw)
        except (ValueError, UnicodeError):
            raise SubscriptionError("Provide a JSON object under 32 KB.") from None

    async def callback(request):
        async def action(username):
            body = await read_body(request)
            callback_url = body.get("url_or_code")
            if not isinstance(callback_url, str):
                raise SubscriptionError("Paste the complete callback URL, including state.")
            await auth.callback(username, callback_url.strip())
            activate_subscription_provider(ctx)
            return {"success": True}

        return await guarded(request, action)

    async def disconnect(request):
        async def action(username):
            grant = auth.store.load(username)
            auth.disconnect(username)
            # Leave other users' wrapper and API-key fallback intact.
            revoked = False
            if grant and grant.get("refresh_token") and grant.get("issuer") == auth.options.issuer:
                with contextlib.suppress(SubscriptionError):
                    revoked = await auth.http.revoke(grant)
            return {"success": True, "connected": False, "remote_revocation_confirmed": revoked}

        return await guarded(request, action)

    async def import_local(request):
        async def action(username):
            options = auth.options
            if (
                not callable(options.local_credentials_path)
                or not callable(options.can_import_local_credentials)
                or not options.can_import_local_credentials(request)
            ):
                raise SubscriptionError("Local credential import is disabled by this host.")
            _, generation, fingerprint = auth.store.snapshot(username)
            path = options.local_credentials_path(username)
            imported = read_file(path) if path else None
            if not imported:
                raise SubscriptionError("No application credentials are available for this user.")
            client = imported.get("client_id")
            if not isinstance(client, str) or client == "dynamic_agent_client" or not client.startswith("oaiapp_"):
                raise SubscriptionError(
                    "Import credentials issued for this application with their client_id. Operator CLI credentials are not compatible."
                )
            tokens = imported.get("tokens") or imported
            claims = await auth.identity.verify(tokens.get("id_token"), client)
            response = dict(tokens)
            # Expiry and scopes come from the explicit trusted application's record,
            # never from unsigned access-token claims.
            response["scope"] = imported.get("scope", tokens.get("scope", ""))
            remaining = imported.get("expires_at", 0) - time.time()
            response["expires_in"] = max(0, min(86400, int(remaining)))
            credentials = auth.credentials(response, client, claims)
            if auth.closed:
                raise SubscriptionError("Subscription service is shutting down.")
            auth.store.save(username, credentials, (generation, fingerprint))
            activate_subscription_provider(ctx)
            return {"success": True, "connected": True}

        return await guarded(request, action)

    async def models(request):
        return await guarded(request, auth.models)

    ctx.add_get("status", status)
    ctx.add_get("models", models)
    ctx.add_post("connect", connect)
    ctx.add_post("callback_manual", callback)
    ctx.add_post("disconnect", disconnect)
    ctx.add_post("import_codex", import_local)


async def load(ctx):
    # Installing the dispatcher doesn't select or expose any user's grant.
    activate_subscription_provider(ctx)


async def reload_providers_hook(ctx):
    activate_subscription_provider(ctx)


__install__ = install
__load__ = load
__reload_providers__ = reload_providers_hook
