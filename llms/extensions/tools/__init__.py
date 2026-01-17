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

    def prop_def_types(prop_def):
        prop_type = prop_def.get("type")
        if not prop_type:
            any_of = prop_def.get("anyOf")
            if any_of:
                return [item.get("type") for item in any_of]
            else:
                return []
        return [prop_type]

    def tool_prop_value(value, prop_def):
        """
        Convert a value to the specified type.
        types: string, number, integer, boolean, object, array, null
        example prop_def = [
            {
                "type": "string"
            },
            {
                "default": "name",
                "type": "string",
                "enum": ["name", "size"]
            },
            {
                "default": [],
                "type": "array",
                "items": {
                    "type": "string"
                }
            },
            {
                "anyOf": [
                    {
                        "type": "string"
                    },
                    {
                        "type": "null"
                    }
                ],
                "default": null,
            },
        ]
        """
        if value is None:
            default = prop_def.get("default")
            if default is not None:
                default = tool_prop_value(default, prop_def)
            return default

        prop_types = prop_def_types(prop_def)
        if "integer" in prop_types:
            return int(value)
        elif "number" in prop_types:
            return float(value)
        elif "boolean" in prop_types:
            return bool(value)
        elif "object" in prop_types:
            return value if isinstance(value, dict) else json.loads(value)
        elif "array" in prop_types:
            return value if isinstance(value, list) else value.split(",")
        else:
            enum = prop_def.get("enum")
            if enum and value not in enum:
                raise Exception(f"'{value}' is not in {enum}")
            return value

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
        function_args = {}
        parameters = tool_def.get("function", {}).get("parameters")
        if parameters:
            properties = parameters.get("properties")
            required_props = parameters.get("required", [])
            if properties:
                for prop_name, prop_def in properties.items():
                    prop_title = prop_def.get("title", prop_name)
                    prop_types = prop_def_types(prop_def)
                    value = None
                    if prop_name in args:
                        value = tool_prop_value(args[prop_name], prop_def)
                    elif prop_name in required_props:
                        if "null" in prop_types:
                            value = None
                        elif "default" in prop_def:
                            value = tool_prop_value(prop_def["default"], prop_def)
                        else:
                            raise Exception(f"Missing required parameter '{prop_title}' for tool '{name}'")
                    if value is not None or "null" in prop_types:
                        function_args[prop_name] = value
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
