import json
import os

from aiohttp import web


def install(ctx):
    async def tools_handler(request):
        groups = {k: list(v) for k, v in ctx.app.tool_groups.items()}
        definitions = list(ctx.app.tool_definitions)
        if getattr(ctx.app, "contextual_tool_providers", None):
            context = {"request": request, "user": ctx.get_username(request)}
            for name, handle in (await ctx.app.resolve_contextual_tools(context, "__list")).items():
                definitions.append(handle["definition"])
                groups.setdefault(handle["tool"]["group"], []).append(name)
        return web.json_response(
            {
                "groups": groups,
                "definitions": definitions,
            }
        )

    ctx.add_get("", tools_handler)

    async def exec_handler(request):
        name = request.match_info.get("name")
        args = await request.json()

        if name.startswith("mcp_") and getattr(ctx.app, "mcp_client", None):
            from llms.extensions.mcp_client.common import McpError
            try:
                ctx.app.mcp_client.mutation(request)
                context = {"request": request, "user": ctx.get_username(request)}
                handles = await ctx.app.resolve_contextual_tools(context, name)
                handle = handles.get(name)
                if not handle:
                    raise McpError("access_denied", "Remote tool is unavailable")
                result = await handle["provider"].invoke(handle, args, context)
                return web.json_response([{"type": "text", "text": json.dumps(result)}, *result.get("resources", [])])
            except McpError as exc:
                return web.json_response({"responseStatus": {"errorCode": exc.code, "message": str(exc)}}, status=403)

        tool_def = ctx.get_tool_definition(name)
        if not tool_def:
            raise Exception(f"Tool '{name}' not found")

        type = tool_def.get("type")
        if type != "function":
            raise Exception(f"Tool '{name}' of type '{type}' is not supported")

        ctx.dbg(f"Executing tool '{name}' with args:\n{json.dumps(args, indent=2)}")

        # Filter args against tool properties
        function_args = {}
        parameters = tool_def.get("function", {}).get("parameters")
        if parameters:
            properties = parameters.get("properties")
            if properties:
                for key in args:
                    if key in properties:
                        function_args[key] = args[key]
            else:
                ctx.dbg(f"tool '{name}' has no properties:\n{json.dumps(tool_def, indent=2)}")
        else:
            ctx.dbg(f"tool '{name}' has no parameters:\n{json.dumps(tool_def, indent=2)}")

        try:
            text, resources = await ctx.exec_tool(name, function_args)

            results = []
            if text:
                results.append(
                    {
                        "type": "text",
                        "text": text,
                    }
                )
            if resources:
                results.extend(resources)

            return web.json_response(results)
        except Exception as e:
            ctx.err(f"Failed to execute tool '{name}' with args:\n{json.dumps(function_args, indent=2)}", e)
            raise e

    ctx.add_post("exec/{name}", exec_handler)

    async def server_tools_handler(request):
        user = ctx.get_username(request)
        paths = []
        if user:
            user_path = ctx.app.get_user_path(user)
            paths.append(os.path.join(user_path, "server-tools.json"))

        default_path = ctx.app.get_user_path("default")
        paths.append(os.path.join(default_path, "server-tools.json"))
        paths.append(os.path.join(ctx.path, "ui", "server-tools.json"))

        for path in paths:
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    return web.json_response(data)
                except Exception as e:
                    ctx.err(f"Error reading tools from {path}", e)

        return web.json_response([])

    ctx.add_get("server", server_tools_handler)


__install__ = install
