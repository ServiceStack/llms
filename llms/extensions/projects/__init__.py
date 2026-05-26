import json
import os

from aiohttp import web


def install(ctx):
    # helper to get user or default projects
    def get_user_projects(request):
        candidate_paths = []
        # check if user is signed in
        user = ctx.get_username(request)
        if user:
            # if signed in (Github OAuth), return the prompts for this user if exists
            candidate_paths.append(os.path.join(ctx.get_user_path(user), "projects", "projects.json"))
        # return default prompts for all users if exists
        candidate_paths.append(os.path.join(ctx.get_user_path(), "projects", "projects.json"))

        # iterate all candidate paths and when exists return its json
        for path in candidate_paths:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    txt = f.read()
                    try:
                        return json.loads(txt)
                    except Exception as e:
                        ctx.err("Failed to parse projects.json", e)
                        return []
        return []

    # API Handler to get prompts
    async def get_projects(request):
        projects_json = get_user_projects(request)
        return web.json_response(projects_json)

    active_project = None
    initial_cwd = os.getcwd()

    async def get_active_project(request):
        nonlocal active_project
        return web.json_response({
            "active": active_project,
            "defaultPath": initial_cwd
        })

    async def set_active_project(request):
        nonlocal active_project
        try:
            data = await request.json()
            path = data.get("path")
            name = data.get("name")

            if not path:
                # Switch back to initial working directory
                os.chdir(initial_cwd)
                active_project = None
                ctx.log(f"Switched back to default workspace directory: {initial_cwd}")
                return web.json_response({"status": "ok", "active": None})

            # Switch working directory
            if not os.path.exists(path) or not os.path.isdir(path):
                return web.json_response({"error": f"Directory does not exist: {path}"}, status=400)

            os.chdir(path)
            # Add to allowed directories
            ctx.add_allowed_directory(path)

            active_project = {
                "name": name,
                "path": path
            }
            ctx.log(f"Switched active project to '{name}' at: {path}")
            return web.json_response({"status": "ok", "active": active_project})
        except Exception as e:
            ctx.err("Failed to change active project", e)
            return web.json_response({"error": str(e)}, status=500)

    ctx.add_get("projects.json", get_projects)
    ctx.add_get("active", get_active_project)
    ctx.add_post("active", set_active_project)


__install__ = install
