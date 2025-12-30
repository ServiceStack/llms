import json
import os

from aiohttp import web

default_prompts = [
    {"name": "Helpful Assistant", "prompt": "You are a helpful assistant."},
]


# runs after providers are configured but before server is run
def install(ctx):
    # helper to get user or default prompts
    def get_user_prompts(request):
        candidate_paths = []
        # check if user is signed in
        username = ctx.get_username(request)
        if username:
            # if signed in (Github OAuth), return the prompts for this user if exists
            candidate_paths.append(os.path.join(ctx.get_user_path(username), "system_prompts", "prompts.json"))
        # return default prompts for all users if exists
        candidate_paths.append(os.path.join(ctx.get_user_path(), "system_prompts", "prompts.json"))
        # otherwise return the default prompts from this repo
        candidate_paths.append(os.path.join(ctx.path, "ui", "prompts.json"))

        # iterate all candidate paths and when exists return its json
        for path in candidate_paths:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    txt = f.read()
                    return json.loads(txt)
        return default_prompts

    # API Handler to get prompts
    async def get_prompts(request):
        prompts_json = get_user_prompts(request)
        return web.json_response(prompts_json)

    ctx.add_get("prompts.json", get_prompts)


# register install extension handler
__install__ = install

__order__ = -10
