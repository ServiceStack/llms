"""Project output discovery shared by independently enabled sharing extensions."""

import os
import re

from aiohttp import web


def is_path_within(path: str, directory: str) -> bool:
    """Return whether path is inside directory, including Windows drive/case rules."""
    path = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    directory = os.path.normcase(os.path.realpath(os.path.abspath(directory)))
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


def sanitize_publish_path(publish: str | None, project_dir: str | None = None) -> str:
    if not publish or not publish.strip():
        return ""
    publish = publish.strip()

    if project_dir:
        abs_project = os.path.abspath(project_dir)
        project_folder_name = os.path.basename(abs_project)

        if os.path.isabs(publish):
            abs_publish = os.path.abspath(publish)
            if os.path.normcase(abs_publish) == os.path.normcase(abs_project):
                return ""
            if is_path_within(abs_publish, abs_project):
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
        path = subparts[1] if len(subparts) > 1 else ""

    parts = [p for p in re.split(r"[/\\]+", path) if p and p != "." and p != ".."]
    return "/".join(parts)


def kebab_case(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-").lower()


def register_project_output_routes(ctx):
    async def detect_dist(request):
        user = ctx.get_username(request)
        active_project = ctx.get_user_pref("project", user=user)
        user_projects = ctx.projects.get_user_projects(user) if hasattr(ctx, "projects") else []
        if request.query.get("threadId"):
            thread = ctx.threads.get_thread(request.query["threadId"], user)
            if not thread:
                raise web.HTTPNotFound(text="Thread not found")
            proj = next((p for p in user_projects if p.get("id") == thread.get("projectId")), None)
        else:
            proj = next((p for p in user_projects if p.get("name") == active_project), None) if active_project else None

        if proj:
            folder = proj.get("folder") or kebab_case(proj.get("name", ""))
            project_dir = os.path.abspath(os.path.join(ctx.get_user_path(user), "projects", folder))
            publish_prop = sanitize_publish_path(proj.get("publish"), project_dir)

            if publish_prop:
                return web.json_response({"dist": publish_prop})

            dist_path = os.path.join(project_dir, "dist")
            if os.path.exists(dist_path) and os.path.isdir(dist_path):
                return web.json_response({"dist": "dist"})
            return web.json_response({"dist": ""})

        return web.json_response({"dist": ""})

    ctx.add_get("detect-dist", detect_dist)

    async def list_subdirs(request):
        user = ctx.get_username(request)
        path_param = request.query.get("path", "")
        project_param = request.query.get("project", "")

        active_project = project_param or ctx.get_user_pref("project", user=user)
        project_dir = None
        proj = None
        if active_project:
            user_projects = ctx.projects.get_user_projects(user) if hasattr(ctx, "projects") else []
            proj = next((p for p in user_projects if p.get("name") == active_project or p.get("folder") == active_project), None)
            if proj:
                folder = proj.get("folder") or kebab_case(proj.get("name", ""))
                project_dir = os.path.abspath(os.path.join(ctx.get_user_path(user), "projects", folder))

        if not project_dir:
            project_dir = os.path.abspath(ctx.get_user_path(user))

        clean_rel = sanitize_publish_path(path_param, project_dir)
        resolved_path = os.path.abspath(os.path.join(project_dir, clean_rel))

        if not is_path_within(resolved_path, project_dir) or not os.path.exists(resolved_path) or not os.path.isdir(resolved_path):
            return web.json_response({"error": "Invalid or non-existent path", "path": path_param}, status=400)

        try:
            subdirs = []
            for item in os.listdir(resolved_path):
                full_path = os.path.join(resolved_path, item)
                if os.path.isdir(full_path) and not item.startswith("."):
                    rel_sub = os.path.relpath(full_path, project_dir)
                    subdirs.append({"name": item, "path": rel_sub})
            subdirs.sort(key=lambda x: x["name"].lower())

            rel_current = os.path.relpath(resolved_path, project_dir)
            if rel_current == ".":
                rel_current = ""

            parent_path = None
            if resolved_path != project_dir:
                parent_abs = os.path.dirname(resolved_path)
                if is_path_within(parent_abs, project_dir):
                    rel_parent = os.path.relpath(parent_abs, project_dir)
                    parent_path = "" if rel_parent == "." else rel_parent

            user_projects_dir = os.path.abspath(os.path.join(ctx.get_user_path(user), "projects"))
            if is_path_within(resolved_path, user_projects_dir):
                rel_proj = os.path.relpath(resolved_path, user_projects_dir)
                display_path = "~/" if rel_proj == "." else f"~/{rel_proj}"
            elif proj:
                folder_name = proj.get("folder") or kebab_case(proj.get("name", ""))
                display_path = f"~/{folder_name}" + (f"/{rel_current}" if rel_current else "")
            else:
                display_path = "~/" + os.path.basename(resolved_path)

            return web.json_response(
                {
                    "currentPath": rel_current,
                    "displayPath": display_path,
                    "parentPath": parent_path,
                    "subdirs": subdirs,
                }
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    ctx.add_get("list-subdirs", list_subdirs)

