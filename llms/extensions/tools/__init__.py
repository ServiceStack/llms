import json

from aiohttp import web


def install(ctx):
    async def tools_handler(request):
        return web.json_response(
            {
                "groups": ctx.app.tool_groups,
                "definitions": ctx.app.tool_definitions,
            }
        )

    ctx.add_get("", tools_handler)

    async def exec_handler(request):
        name = request.match_info.get("name")
        args = await request.json()

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


__install__ = install
