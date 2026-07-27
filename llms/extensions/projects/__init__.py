import json
import os
import re
from typing import Optional

from aiohttp import web


def kebab_case(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-").lower()


def sanitize_publish_path(publish: Optional[str], project_dir: Optional[str] = None) -> str:
    if not publish or not publish.strip():
        return ""
    publish = publish.strip()

    if project_dir:
        abs_project = os.path.abspath(project_dir)
        project_folder_name = os.path.basename(abs_project)

        if os.path.isabs(publish):
            abs_publish = os.path.abspath(publish)
            if abs_publish == abs_project:
                return ""
            if abs_publish.startswith(abs_project + os.sep):
                rel = os.path.relpath(abs_publish, abs_project)
                parts = [p for p in re.split(r"[/\\]+", rel) if p and p != "." and p != ".."]
                return "/".join(parts)

        clean = publish.lstrip("/\\")
        if clean == project_folder_name or clean == f"projects/{project_folder_name}":
            return ""
        if clean.startswith(f"projects/{project_folder_name}/"):
            clean = clean[len(f"projects/{project_folder_name}/"):]
        elif clean.startswith(f"{project_folder_name}/"):
            clean = clean[len(f"{project_folder_name}/"):]

        parts = [p for p in re.split(r"[/\\]+", clean) if p and p != "." and p != ".."]
        return "/".join(parts)

    path = publish.lstrip("/\\")
    if "projects/" in path:
        parts_path = path.split("projects/")[-1]
        subparts = parts_path.split("/", 1)
        if len(subparts) > 1:
            path = subparts[1]
        else:
            path = ""

    parts = [p for p in re.split(r"[/\\]+", path) if p and p != "." and p != ".."]
    return "/".join(parts)


def install(ctx):
    def get_project_folder(project: dict) -> str:
        folder = project.get("folder")
        if folder and folder.strip():
            return folder.strip()
        return kebab_case(project.get("name", ""))

    def get_project_dir(user: Optional[str], project: dict) -> str:
        folder = get_project_folder(project)
        return os.path.join(ctx.get_user_path(user), "projects", folder)

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
                        for project in projects:
                            if "folder" not in project or not project["folder"]:
                                project["folder"] = get_project_folder(project)
                            p_dir = get_project_dir(user, project)
                            if "publish" in project:
                                project["publish"] = sanitize_publish_path(project.get("publish"), p_dir)
                        return projects
                    except Exception as e:
                        ctx.err("Failed to parse projects.json", e)
        return []

    # API Handler to get projects
    async def get_projects(request):
        user = ctx.get_username(request)
        return web.json_response(get_user_projects(user))

    ctx.add_get("projects.json", get_projects)

    # API Handler to save projects
    async def save_projects(request):
        user = ctx.get_username(request)
        projects = await request.json()

        if user:
            path = os.path.join(ctx.get_user_path(user), "projects", "projects.json")
        else:
            path = os.path.join(ctx.get_user_path(), "projects", "projects.json")

        # Create folders for non-existent paths and update folder field
        for project in projects:
            if "folder" not in project or not project["folder"]:
                project["folder"] = get_project_folder(project)
            project_dir = get_project_dir(user, project)
            if "publish" in project:
                project["publish"] = sanitize_publish_path(project.get("publish"), project_dir)
            project.pop("paths", None)
            try:
                if not os.path.exists(project_dir):
                    os.makedirs(project_dir, exist_ok=True)
                    ctx.log(f"Created directory: {project_dir}")
            except Exception as e:
                ctx.err(f"Failed to create directory {project_dir}", e)

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

        if "folder" not in project_data or not project_data["folder"]:
            project_data["folder"] = get_project_folder(project_data)
        project_dir = get_project_dir(user, project_data)
        if "publish" in project_data:
            project_data["publish"] = sanitize_publish_path(project_data.get("publish"), project_dir)
        project_data.pop("paths", None)

        project_dir = get_project_dir(user, project_data)
        try:
            if not os.path.exists(project_dir):
                os.makedirs(project_dir, exist_ok=True)
                ctx.log(f"Created directory: {project_dir}")
        except Exception as e:
            ctx.err(f"Failed to create directory {project_dir}", e)

        projects = get_user_projects(user)

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
        return web.json_response(projects)

    ctx.add_post("save/{name}", save_project)

    def set_project_directories(project_name: str, user: Optional[str] = None):
        user_projects = get_user_projects(user)
        project_paths = []
        if project_name:
            matching = [p for p in user_projects if p.get("name") == project_name]
            if matching:
                project_dir = get_project_dir(user, matching[0])
                project_paths = [project_dir]
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

    # first time user setup
    async def setup_user(request):
        user = ctx.get_username(request)
        ctx.log(f"First time projects user setup for '{user}' user")
        active_project = ctx.get_user_pref("project", user=user)
        project_paths = set_project_directories(active_project, user)
        ctx.log(f"Projects [{user}] {active_project}: {project_paths}")

    ctx.register_setup_user_handler(setup_user)

    class ProjectsApi:
        def get_user_projects(self, user: Optional[str] = None):
            return get_user_projects(user)

    ctx.projects = ProjectsApi()


__install__ = install
