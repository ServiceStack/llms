"""Local OAuth callback receiver. Codes and tokens never enter access logs."""

import asyncio
import errno
import html
import json
from urllib.parse import urlsplit

from aiohttp import web

from llms.extensions.openai_auth.security import SubscriptionError


def callback_page(return_url=None, error=None):
    title = "ChatGPT sign-in failed" if error else "Connected to ChatGPT"
    message = error or "Sign-in complete. You can return to the app."
    link = f'<p><a href="{html.escape(return_url, quote=True)}">Return to app</a></p>' if return_url else ""
    script = ""
    if not error:
        # Escape HTML characters as well as JS syntax; a URL must never terminate the script element.
        target = json.dumps(return_url).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        script = "<script>history.replaceState(null,'',location.pathname);window.close();"
        if return_url:
            script += f"setTimeout(()=>location.replace({target}),100);"
        script += "</script>"
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
        f"<title>{title}</title></head><body><h1>{title}</h1><p>{html.escape(message)}</p>{link}{script}</body></html>"
    )


class CallbackReceiver:
    def __init__(self, auth, connected):
        self.auth, self.connected = auth, connected
        self.runner, self.redirect = None, None
        self.gate = asyncio.Lock()
        self.closed = False

    async def start(self):
        async with self.gate:
            if self.closed or self.auth.closed:
                raise SubscriptionError("Subscription service is shutting down.")
            if self.runner:
                return self.redirect
            try:
                uri = urlsplit(self.auth.options.redirect_uri)
                if (
                    uri.scheme != "http"
                    or uri.hostname != "127.0.0.1"
                    or uri.path != "/auth/callback"
                    or uri.username is not None
                    or uri.query
                    or uri.fragment
                ):
                    raise ValueError()
                port = uri.port if uri.port is not None else 80
            except ValueError:
                raise SubscriptionError(
                    "Configure http://127.0.0.1:<port>/auth/callback for automatic sign-in."
                ) from None
            app = web.Application(client_max_size=0)
            app.router.add_get("/auth/callback", self.receive)
            runner = web.AppRunner(app, access_log=None, max_line_size=16384, shutdown_timeout=5)
            await runner.setup()
            try:
                try:
                    await web.TCPSite(runner, "127.0.0.1", port).start()
                except OSError as error:
                    if error.errno != errno.EADDRINUSE:
                        raise
                    await web.TCPSite(runner, "127.0.0.1", 0).start()
                # Only the successfully bound site has a socket in runner.addresses.
                bound = runner.addresses[0][1]
                self.redirect = f"http://127.0.0.1:{bound}/auth/callback"
                self.runner = runner
                return self.redirect
            except OSError:
                await runner.cleanup()
                raise SubscriptionError("Could not start the OpenAI callback listener on 127.0.0.1.") from None
            except BaseException:
                await runner.cleanup()
                raise

    async def receive(self, request):
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
        if not self.redirect or request.host != urlsplit(self.redirect).netloc:
            return web.Response(status=404, headers=headers)
        try:
            user, return_url = await self.auth.automatic_callback(str(request.url))
            self.connected(user)
            return web.Response(text=callback_page(return_url), content_type="text/html", headers=headers)
        except SubscriptionError as error:
            return web.Response(
                text=callback_page(error=str(error)), status=400, content_type="text/html", headers=headers
            )

    async def close(self):
        async with self.gate:
            self.closed = True
            if self.runner:
                await self.runner.cleanup()
                self.runner = None
