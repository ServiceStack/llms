import json
import os
from typing import Optional

from aiohttp import web


def install(ctx):
    def get_user_projects(user: Optional[str] = None):
        candidate_paths = []
        if user:
            candidate_paths.append(os.path.join(ctx.get_user_path(user), "projects", "projects.json"))
        candidate_paths.append(os.path.join(ctx.get_user_path(), "projects", "projects.json"))

        # iterate all candidate paths and when exists return its json
        for path in candidate_paths:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    txt = f.read()
                    try:
                        projects = json.loads(txt)
                        return projects
                    except Exception as e:
                        ctx.err("Failed to parse projects.json", e)
        return []

    # API Handler to get prompts
    async def get_projects(request):
        user = ctx.get_username(request)
        projects_json = get_user_projects(user)
        return web.json_response(projects_json)

    ctx.add_get("projects.json", get_projects)

    # API Handler to save projects
    async def save_projects(request):
        user = ctx.get_username(request)
        projects = await request.json()

        if user:
            path = os.path.join(ctx.get_user_path(user), "projects", "projects.json")
        else:
            path = os.path.join(ctx.get_user_path(), "projects", "projects.json")

        # Create folders for non-existent paths
        for project in projects:
            for p in project.get("paths", []):
                if not p or not p.strip():
                    continue
                resolved_path = ctx.resolve_directory(p)
                if resolved_path:
                    try:
                        if not os.path.exists(resolved_path):
                            os.makedirs(resolved_path, exist_ok=True)
                            ctx.log(f"Created directory: {resolved_path}")
                    except Exception as e:
                        ctx.err(f"Failed to create directory {resolved_path}", e)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(projects, f, indent=2, ensure_ascii=False)

        # If active project is deleted, reset the preference
        active_project = ctx.get_user_pref("project", user=user)
        if active_project and not any(p.get("name") == active_project for p in projects):
            ctx.set_user_pref("project", None, user=user)
            set_project_directories(None, user)
            ctx.log(f"Active project '{active_project}' was deleted, resetting active project.")

        ctx.log(f"Saved projects for {user or 'default'} to {path}")
        return web.json_response(projects)

    ctx.add_post("projects.json", save_projects)

    async def save_project(request):
        user = ctx.get_username(request)
        name = request.match_info.get("name")
        project_data = await request.json()

        if not project_data or not project_data.get("name"):
            return web.json_response({"error": "Project name is required"}, status=400)

        projects = get_user_projects(user)

        # Create folders for non-existent paths
        for p in project_data.get("paths", []):
            if not p or not p.strip():
                continue
            resolved_path = ctx.resolve_directory(p)
            if resolved_path:
                try:
                    if not os.path.exists(resolved_path):
                        os.makedirs(resolved_path, exist_ok=True)
                        ctx.log(f"Created directory: {resolved_path}")
                except Exception as e:
                    ctx.err(f"Failed to create directory {resolved_path}", e)

        # Find the project with the name matching URL parameter `name`
        found_idx = -1
        for idx, p in enumerate(projects):
            if p.get("name") == name:
                found_idx = idx
                break

        if found_idx != -1:
            projects[found_idx] = project_data
        else:
            projects.append(project_data)

        if user:
            path = os.path.join(ctx.get_user_path(user), "projects", "projects.json")
        else:
            path = os.path.join(ctx.get_user_path(), "projects", "projects.json")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(projects, f, indent=2, ensure_ascii=False)

        # Handle active project naming update if renamed
        active_project = ctx.get_user_pref("project", user=user)
        if active_project == name:
            new_name = project_data.get("name")
            if new_name and new_name != active_project:
                ctx.set_user_pref("project", new_name, user=user)
                set_project_directories(new_name, user)
                ctx.log(f"Renamed active project from '{active_project}' to '{new_name}'")

        ctx.log(f"Saved project '{name}' for {user or 'default'} to {path}")
        return web.json_response(project_data)

    ctx.add_post("save/{name}", save_project)

    def set_project_directories(project_name: str, user: Optional[str] = None):
        user_projects = get_user_projects(user)
        project_paths = ctx.get_allowed_directories()
        if project_name:
            project_paths = [p["paths"] for p in user_projects if p["name"] == project_name][0] if project_name else []
        ctx.set_allowed_directories(project_paths, user)
        return project_paths

    async def set_active_project(request):
        user = ctx.get_username(request)
        user_projects = get_user_projects(user)
        data = await request.json()
        name = data.get("name")
        if name is None:
            ctx.set_user_pref("project", None, user=user)
            set_project_directories(name, user)
            ctx.log("Unselected active project")
            return web.json_response(None)

        project = next((p for p in user_projects if p["name"] == name), None)
        if project is None:
            raise Exception(f"Project '{name}' not found")

        ctx.set_user_pref("project", project["name"], user=user)
        project_paths = set_project_directories(name, user)
        ctx.log(f"Switched active project to '{name}': {project_paths}")
        return web.json_response(project)

    ctx.add_post("active", set_active_project)

    # async def chat_request(openai_request, context):
    #     chat = openai_request
    #     user = context.get("user", None)
    #     metadata = chat.get("metadata", {})
    #     tools = metadata.get("tools")

    #     active_project = ctx.get_user_pref("project", user=user)
    #     project_paths = set_project_directories(active_project, user)
    #     user_projects = get_user_projects(user)

    #     ctx.log(f"Projects [user]: {user}, [tools]: {tools}")
    #     ctx.log(f"Projects Meta: {metadata}")
    #     ctx.log(f"Projects User: {user_projects}")
    #     ctx.log(f"Projects [{user}], active: {active_project} | {project_paths}")
    #     ctx.log(f"Projects [{user}], resolved: {ctx.resolve_allowed_directories(user)}")
    # ctx.register_chat_request_filter(chat_request)

    # first time user setup
    async def setup_user(request):
        user = ctx.get_username(request)
        ctx.log(f"First time projects user setup for '{user}' user")
        set_project_directories(None, user)
        active_project = ctx.get_user_pref("project", user=user)
        project_paths = set_project_directories(active_project, user)
        ctx.log(f"Projects [{user}] {active_project}: {project_paths}")

    ctx.register_setup_user_handler(setup_user)

    class ProjectsApi:
        def get_user_projects(self, user: Optional[str] = None):
            return get_user_projects(user)

    ctx.projects = ProjectsApi()


__install__ = install
