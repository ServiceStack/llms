from aiohttp import web


def install(ctx):
    ctx.add_get("/", lambda r: web.json_response(ctx.app.tool_definitions))
