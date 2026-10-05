"""Account-free project exports, configured globally by the host."""

import asyncio
import json
import os
from pathlib import Path

from aiohttp import web

from llms.extensions.projects.publishing import register_project_output_routes

from .static import publish_folder, settings


def install(ctx):
    config_path = Path(ctx.get_user_path()) / "share_static/config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("share_static config must be an object")
    aliases = getattr(ctx.app, "aliased_directories", {})
    startup = aliases.get("$WORKSPACE", os.getcwd()) if isinstance(aliases, dict) else os.getcwd()
    static_config = settings(config, startup)

    async def get_config(request):
        return web.json_response(static_config)

    async def publish(request):
        authenticated, _ = ctx.check_auth(request)
        if not authenticated:
            raise web.HTTPUnauthorized(text="Authentication required")
        try:
            result = await asyncio.to_thread(publish_folder, ctx, ctx.get_username(request), request.match_info["id"], static_config)
        except (OSError, UnicodeError) as e:
            raise web.HTTPInternalServerError(text=f"Unable to publish project to {static_config['directory']}: {e}") from e
        return web.json_response(result)

    ctx.add_get("config.json", get_config)
    ctx.add_post("project/{id}/folder", publish)
    register_project_output_routes(ctx)


__install__ = install
