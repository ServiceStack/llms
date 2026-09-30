"""Desktop-only adapter around the unchanged llms-py aiohttp server.

The regular package constructs its complete application and finally calls
``aiohttp.web.run_app``. The frozen desktop entrypoint replaces only that
process-level runner. This keeps every desktop concern out of ``llms/main.py``
while still receiving the fully configured application, including extensions.
"""

import asyncio
import builtins
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
from typing import Any, Callable, Dict

from aiohttp import web


HOST = "127.0.0.1"
TOKEN_ENV = "LLMS_DESKTOP_TOKEN"
COOKIE = "llms_desktop_session"
HEALTH_PATH = "/~desktop/health"
CAPABILITIES_PATH = "/~desktop/capabilities"
SHUTDOWN_PATH = "/~desktop/shutdown"
PREFERENCES_PATH = "/~desktop/preferences"
PREFERENCES_LOCK = web.AppKey("desktop_preferences_lock", asyncio.Lock)
BOOTSTRAP_PREFIX = "/~desktop/bootstrap/"
# The desktop UI is going away: do not wait aiohttp's default 60 seconds
# for long-lived SSE/chat requests before cancelling them and running cleanup.
SHUTDOWN_TIMEOUT = 1.0
BOOTSTRAP_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0;url=/">
<title>Starting llms.py</title>
</head>
<body><p>Starting llms.py…</p></body>
</html>
"""

_port = 0
_token = ""
_stop_event = None
_version = ""


def compare_secret(left: str, right: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(left.encode("utf-8")).digest(),
        hashlib.sha256(right.encode("utf-8")).digest(),
    )


def capabilities() -> Dict[str, Any]:
    detected_tools = {}
    for name in ("git", "uv", "ffmpeg", "typst", "dotnet", "bun"):
        path = shutil.which(name)
        detected_tools[name] = {"available": bool(path), "path": path}
    return {
        "runtime": {
            "python": True,
            "frozen": bool(getattr(sys, "frozen", False)),
            "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        },
        "tools": detected_tools,
    }


def secure(response: web.StreamResponse) -> web.StreamResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


async def desktop_response_prepare(_request: web.Request, response: web.StreamResponse) -> None:
    """Apply desktop headers before normal and streaming responses are sent."""
    secure(response)


@web.middleware
async def desktop_middleware(request: web.Request, handler: Callable) -> web.StreamResponse:
    expected_host = f"{HOST}:{_port}"
    if request.host != expected_host:
        return secure(web.json_response({"error": "Invalid host"}, status=403))

    origin = request.headers.get("Origin")
    if origin and origin != f"http://{expected_host}":
        return secure(web.json_response({"error": "Invalid origin"}, status=403))

    if request.path == HEALTH_PATH:
        return secure(
            web.json_response(
                {"status": "ready", "desktop": True, "version": _version, "pid": os.getpid()}
            )
        )

    if request.path.startswith(BOOTSTRAP_PREFIX):
        supplied_token = request.path[len(BOOTSTRAP_PREFIX) :]
        if not supplied_token or not compare_secret(supplied_token, _token):
            return secure(web.json_response({"error": "Invalid desktop bootstrap token"}, status=401))
        # Commit the Strict session cookie on a same-site document before the
        # WebView navigates to the application. A redirect directly from the
        # packaged tauri:// page can remain cross-site in WebKit's redirect
        # chain, causing it to omit the new Strict cookie on the first request.
        response = web.Response(text=BOOTSTRAP_PAGE, content_type="text/html")
        response.set_cookie(COOKIE, _token, httponly=True, path="/", samesite="Strict")
        return secure(response)

    supplied_token = request.cookies.get(COOKIE) or request.headers.get("X-LLMS-Desktop-Token", "")
    if not supplied_token or not compare_secret(supplied_token, _token):
        return secure(web.json_response({"error": "Desktop session required"}, status=401))

    if request.path == CAPABILITIES_PATH:
        return secure(web.json_response(capabilities()))

    if request.path == PREFERENCES_PATH and request.method == "POST":
        try:
            preferences = await request.json()
        except (ValueError, UnicodeDecodeError):
            return secure(web.json_response({"error": "Invalid preferences"}, status=400))
        color_scheme = preferences.get("colorScheme") if isinstance(preferences, dict) else None
        if color_scheme not in ("light", "dark"):
            return secure(web.json_response({"error": "Invalid color scheme"}, status=400))
        path = os.getenv("LLMS_DESKTOP_PREFERENCES_PATH")
        if not path:
            return secure(web.json_response({"error": "Desktop preferences unavailable"}, status=503))

        def save_preferences():
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps({"colorScheme": color_scheme}), encoding="utf-8")
            temporary.replace(target)

        # Serialize saves to prevent rapid theme changes from racing atomic replacements.
        try:
            async with request.app[PREFERENCES_LOCK]:
                await asyncio.to_thread(save_preferences)
        except OSError:
            return secure(web.json_response({"error": "Could not save desktop preferences"}, status=500))
        return secure(web.json_response({"colorScheme": color_scheme}))

    if request.path == SHUTDOWN_PATH and request.method == "POST":
        if _stop_event is not None:
            asyncio.get_running_loop().call_soon(_stop_event.set)
        return secure(web.json_response({"status": "stopping"}))

    return await handler(request)


async def _serve(app: web.Application, port: int, loop: asyncio.AbstractEventLoop) -> None:
    global _stop_event
    _stop_event = asyncio.Event()
    app[PREFERENCES_LOCK] = asyncio.Lock()
    runner = web.AppRunner(app, shutdown_timeout=SHUTDOWN_TIMEOUT)
    installed_signals = []

    def request_stop() -> None:
        _stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
            installed_signals.append(sig)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        await runner.setup()
        site = web.TCPSite(runner, host=HOST, port=port)
        await site.start()
        builtins.print(
            json.dumps({"event": "ready", "host": HOST, "port": port, "version": _version}),
            flush=True,
        )
        await _stop_event.wait()
    finally:
        for sig in installed_signals:
            loop.remove_signal_handler(sig)
        await runner.cleanup()


def desktop_run_app(app: web.Application, *, port: int, **_kwargs) -> None:
    """Replacement for aiohttp.web.run_app used only by the frozen entrypoint."""
    global _port, _token
    _port = int(port)
    _token = os.getenv(TOKEN_ENV, "")
    if len(_token) < 32:
        raise RuntimeError(f"{TOKEN_ENV} must contain at least 32 random characters")

    app.middlewares.append(desktop_middleware)
    app.on_response_prepare.append(desktop_response_prepare)
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(_serve(app, _port, loop))
    except KeyboardInterrupt:
        pass
    except OSError as error:
        builtins.print(
            json.dumps({"event": "error", "host": HOST, "port": _port, "error": str(error)}),
            flush=True,
        )
        raise


def install_desktop_runtime() -> None:
    """Install the adapter in this private process without modifying llms-py source."""
    global _version
    llms_main = importlib.import_module("llms.main")

    _version = llms_main.VERSION
    llms_main.web.run_app = desktop_run_app
