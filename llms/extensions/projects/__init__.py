import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from threading import RLock, local
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


_projects_lock = RLock()
_project_lock_state = local()


@contextmanager
def locked_projects(root):
    """Serialize file read/modify/write across requests, threads and server processes."""
    with _projects_lock:
        if getattr(_project_lock_state, "held", False):
            yield
            return
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, ".projects.lock"), "a+b") as lock:
            if os.name == "nt":
                import msvcrt
                lock.write(b"0")
                lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            _project_lock_state.held = True
            try:
                yield
            finally:
                _project_lock_state.held = False
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def atomic_projects(path, projects):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(projects, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(ctx):
    # Preserve startup permissions separately from legacy active-project switching.
    # Explorer defaults must never inherit a previously selected project's folder.
    explorer_directories = {"default": tuple(ctx.get_allowed_directories())}

    def user_workspace(user: Optional[str] = None) -> str:
        """A user's private workspace; anonymous requests use the 'default' user's."""
        path = os.path.join(ctx.get_user_path(user), "workspace")
        os.makedirs(path, exist_ok=True)
        return path

    def get_project_folder(project: dict) -> str:
        folder = project.get("folder")
        if folder and folder.strip():
            return folder.strip()
        return kebab_case(project.get("name", ""))

    def get_project_dir(user: Optional[str], project: dict) -> str:
        folder = get_project_folder(project)
        root = os.path.realpath(os.path.join(ctx.get_user_path(user), "projects"))
        directory = os.path.realpath(os.path.join(root, folder))
        try:
            inside = os.path.normcase(os.path.commonpath([root, directory])) == os.path.normcase(root)
        except ValueError:
            inside = False
        if not inside or directory == root:
            raise web.HTTPBadRequest(text="Project folder must be inside the projects directory")
        return directory

    def read_user_projects(user: str | None = None):
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
                        changed = False
                        for project in projects:
                            if not project.get("id"):
                                project["id"] = str(uuid.uuid4())
                                changed = True
                            if "folder" not in project or not project["folder"]:
                                project["folder"] = get_project_folder(project)
                            p_dir = get_project_dir(user, project)
                            if "publish" in project:
                                project["publish"] = sanitize_publish_path(project.get("publish"), p_dir)
                        if changed:
                            atomic_projects(path, projects)
                        return projects
                    except Exception as e:
                        ctx.err("Failed to parse projects.json", e)
                        raise web.HTTPInternalServerError(text="Unable to read project configuration") from e
        return []

    def notify_sidebar():
        """Names, folders and visibility appear in the chat sidebar (see app extension)"""
        notify = getattr(ctx, "notify_sidebar", None)
        if notify:
            notify()

    def get_user_projects(user=None):
        with locked_projects(ctx.get_user_path()):
            return read_user_projects(user)

    def preserve_archive(project, existing=None):
        # Archive state changes through its own endpoint, never through an old form.
        for key in ('archived', 'archivedSidebarVisibility'):
            if existing and key in existing:
                project[key] = existing[key]
            else:
                project.pop(key, None)
        if project.get('archived'):
            project['showInSidebar'] = False
        # Publication status is server-owned and survives stale project edit forms.
        if existing and 'staticPublication' in existing:
            project['staticPublication'] = existing['staticPublication']
        else:
            project.pop('staticPublication', None)

    def preserve_ids(projects, previous, user):
        by_id = {p["id"]: p for p in previous}
        by_name = {p["name"]: p for p in previous}
        seen = set()
        for project in projects:
            existing = by_id.get(project.get("id")) or by_name.get(project.get("name"))
            project["id"] = existing["id"] if existing else str(uuid.uuid4())
            preserve_archive(project, existing)
            if existing and "showInSidebar" not in project and "showInSidebar" in existing:
                project["showInSidebar"] = existing["showInSidebar"]
            if existing and "gitSource" in existing:
                project["gitSource"] = existing["gitSource"]
            else:
                project.pop("gitSource", None)
            if project["id"] in seen:
                raise web.HTTPBadRequest(text="Duplicate project")
            seen.add(project["id"])
        removed = set(by_id) - seen
        from llms.extensions.app.db import AppDB
        g_db = getattr(getattr(ctx, "threads", None), "db", None)
        if isinstance(g_db, AppDB):
            where, params = g_db.get_user_filter(user)
            for project_id in removed:
                active = g_db.db.scalar(
                    "SELECT count(*) FROM thread " + (where or "WHERE 1=1") +
                    " AND projectId=:project AND EXISTS (SELECT 1 FROM agent_run "
                    "WHERE threadId=thread.id AND status IN ('queued','running','waiting_approval'))",
                    {**params, "project": project_id})
                if active:
                    raise web.HTTPConflict(text="A project has an active run")
        return removed

    # API Handler to get projects
    async def get_projects(request):
        user = ctx.get_username(request)
        return web.json_response(get_user_projects(user))

    ctx.add_get("projects.json", get_projects)

    # API Handler to save projects
    async def save_projects(request):
        user = ctx.get_username(request)
        projects = await request.json()
        with locked_projects(ctx.get_user_path()):
            if user:
                path = os.path.join(ctx.get_user_path(user), "projects", "projects.json")
            else:
                path = os.path.join(ctx.get_user_path(), "projects", "projects.json")

            previous = get_user_projects(user)
            # Legacy clients may submit only their active list. Omission must not
            # delete archived projects or detach their conversations.
            submitted = {p.get('id') for p in projects} | {p.get('name') for p in projects}
            projects.extend(p for p in previous if p.get('archived')
                            and p['id'] not in submitted and p['name'] not in submitted)
            preserve_ids(projects, previous, user)

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
            with _projects_lock:
                atomic_projects(path, projects)
            from llms.extensions.app.db import AppDB
            database = getattr(getattr(ctx, "threads", None), "db", None)
            if isinstance(database, AppDB):
                database.reconcile_projects([p["id"] for p in projects], user)

            # If active project is deleted, reset the preference
            active_project = ctx.get_user_pref("project", user=user)
            if active_project and not any(p.get("name") == active_project for p in projects):
                ctx.set_user_pref("project", None, user=user)
                set_project_directories(None, user)
                ctx.log(f"Active project '{active_project}' was deleted, resetting active project.")

            ctx.log(f"Saved projects for {user or 'default'} to {path}")
            notify_sidebar()
            return web.json_response(projects)

    ctx.add_post("projects.json", save_projects)

    async def save_project(request):
        user = ctx.get_username(request)
        name = request.match_info.get("name")
        project_data = await request.json()
        with locked_projects(ctx.get_user_path()):
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
                project_data["id"] = projects[found_idx]["id"]
                preserve_archive(project_data, projects[found_idx])
                if "gitSource" in projects[found_idx]:
                    project_data["gitSource"] = projects[found_idx]["gitSource"]
                else:
                    project_data.pop("gitSource", None)
                if "showInSidebar" not in project_data and "showInSidebar" in projects[found_idx]:
                    project_data["showInSidebar"] = projects[found_idx]["showInSidebar"]
                projects[found_idx] = project_data
            else:
                project_data["id"] = str(uuid.uuid4())
                preserve_archive(project_data)
                project_data.pop("gitSource", None)
                projects.append(project_data)

            if user:
                path = os.path.join(ctx.get_user_path(user), "projects", "projects.json")
            else:
                path = os.path.join(ctx.get_user_path(), "projects", "projects.json")

            os.makedirs(os.path.dirname(path), exist_ok=True)
            with _projects_lock:
                atomic_projects(path, projects)

            # Handle active project naming update if renamed
            active_project = ctx.get_user_pref("project", user=user)
            if active_project == name:
                new_name = project_data.get("name")
                if new_name and new_name != active_project:
                    ctx.set_user_pref("project", new_name, user=user)
                    set_project_directories(new_name, user)
                    ctx.log(f"Renamed active project from '{active_project}' to '{new_name}'")

            ctx.log(f"Saved project '{name}' for {user or 'default'} to {path}")
            notify_sidebar()
            return web.json_response(projects)

    ctx.add_post("save/{name}", save_project)

    async def set_sidebar_visibility(request):
        user = ctx.get_username(request)
        data = await request.json()
        visible = data.get("showInSidebar") if isinstance(data, dict) else None
        if type(visible) is not bool:
            raise web.HTTPBadRequest(text="showInSidebar must be a boolean")
        project_id = request.match_info["id"]
        with locked_projects(ctx.get_user_path()):
            projects = get_user_projects(user)
            project = next((p for p in projects if p.get("id") == project_id), None)
            if project is None:
                raise web.HTTPNotFound(text="Project not found")
            if project.get('archived') and visible:
                raise web.HTTPConflict(text='Unarchive this project before showing it in the sidebar.')
            project["showInSidebar"] = visible
            path = os.path.join(ctx.get_user_path(user) if user else ctx.get_user_path(),
                                "projects", "projects.json")
            atomic_projects(path, projects)
        notify_sidebar()
        return web.json_response(projects)

    ctx.add_patch("sidebar/{id}", set_sidebar_visibility)

    async def order_projects(request):
        user = ctx.get_username(request)
        data = await request.json()
        ids = data.get('ids') if isinstance(data, dict) else None
        if (not isinstance(ids, list) or any(not isinstance(id, str) for id in ids)
                or len(ids) != len(set(ids))):
            raise web.HTTPBadRequest(text='Provide each active project ID once.')
        with locked_projects(ctx.get_user_path()):
            projects = get_user_projects(user)
            active = {p['id']: p for p in projects if not p.get('archived')}
            if set(ids) != set(active):
                raise web.HTTPConflict(text='The project list changed. Refresh it and try again.')
            projects = [active[id] for id in ids] + [p for p in projects if p.get('archived')]
            atomic_projects(os.path.join(ctx.get_user_path(user), 'projects', 'projects.json'), projects)
        notify_sidebar()
        return web.json_response(projects)

    async def archive_project(request):
        user = ctx.get_username(request)
        data = await request.json()
        archived = data.get('archived') if isinstance(data, dict) else None
        if type(archived) is not bool:
            raise web.HTTPBadRequest(text='archived must be a boolean')
        with locked_projects(ctx.get_user_path()):
            projects = get_user_projects(user)
            project = next((p for p in projects if p['id'] == request.match_info['id']), None)
            if project is None:
                raise web.HTTPNotFound(text='Project not found')
            if bool(project.get('archived')) != archived:
                if archived:
                    project['archivedSidebarVisibility'] = project.get('showInSidebar', True)
                    project['showInSidebar'] = False
                    project['archived'] = True
                else:
                    project['showInSidebar'] = project.pop('archivedSidebarVisibility', True)
                    project['archived'] = False
                # Restore at the end of the active list. Keep other folders' order.
                projects.remove(project)
                if archived:
                    projects.append(project)
                else:
                    index = next((i for i, p in enumerate(projects) if p.get('archived')), len(projects))
                    projects.insert(index, project)
                atomic_projects(os.path.join(ctx.get_user_path(user), 'projects', 'projects.json'), projects)
            if archived and ctx.get_user_pref('project', user=user) == project['name']:
                ctx.set_user_pref('project', None, user=user)
                set_project_directories(None, user)
        notify_sidebar()
        return web.json_response(projects)

    ctx.add_post('order', order_projects)
    ctx.add_patch('archive/{id}', archive_project)

    def set_project_directories(project_name: str, user: Optional[str] = None):
        user_projects = get_user_projects(user)
        project_paths = []
        if project_name:
            matching = [p for p in user_projects if p.get("name") == project_name and not p.get('archived')]
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
        if project.get('archived'):
            raise web.HTTPConflict(text='Unarchive this project before selecting it.')

        ctx.set_user_pref("project", project["name"], user=user)
        project_paths = set_project_directories(name, user)
        ctx.log(f"Switched active project to '{name}': {project_paths}")
        return web.json_response(project)

    ctx.add_post("active", set_active_project)

    # first time user setup
    async def setup_user(request):
        user = ctx.get_username(request)
        ctx.log(f"First time projects user setup for '{user}' user")
        explorer_directories.setdefault(user or "default", tuple(ctx.get_allowed_directories(user)))
        active_project = ctx.get_user_pref("project", user=user)
        if any(p.get('archived') and p.get('name') == active_project for p in get_user_projects(user)):
            ctx.set_user_pref('project', None, user=user)
            active_project = None
        project_paths = set_project_directories(active_project, user)
        ctx.log(f"Projects [{user}] {active_project}: {project_paths}")

    ctx.register_setup_user_handler(setup_user)

    class ProjectsApi:
        def update_publication(self, project_id, values, user=None, expected=None):
            with locked_projects(ctx.get_user_path()):
                projects = read_user_projects(user)
                project = next((p for p in projects if p['id'] == project_id), None)
                if not project:
                    raise web.HTTPNotFound(text='Project not found')
                if expected and any(project.get(key) != value for key, value in expected.items()):
                    raise web.HTTPConflict(text='Project output settings changed during publishing. Retry publishing.')
                project.update(values)
                atomic_projects(os.path.join(ctx.get_user_path(user), 'projects', 'projects.json'), projects)

        def creation_destination(self, project, user=None):
            return get_project_dir(user, project)

        def check_creation(self, project, user=None):
            destination = os.path.normcase(get_project_dir(user, project))
            for existing in get_user_projects(user):
                if existing['id'] == project['id']:
                    continue
                if (existing['name'] == project['name']
                        or os.path.normcase(get_project_dir(user, existing)) == destination):
                    raise web.HTTPConflict(text='A project with that name or folder already exists.')

        def register_created(self, project, user, temporary, operation_id, marker):
            with locked_projects(ctx.get_user_path()):
                projects = get_user_projects(user)
                existing = next((p for p in projects if p['id'] == project['id']), None)
                if existing:
                    proof = os.path.join(get_project_dir(user, existing), marker)
                    if os.path.isfile(proof) and not os.path.islink(proof):
                        with open(proof, encoding='utf-8') as f:
                            verified = f.read() == operation_id
                        if verified:
                            os.unlink(proof)
                    return {'project': existing, 'projects': projects}
                self.check_creation(project, user)
                destination = get_project_dir(user, project)
                if os.path.lexists(destination):
                    proof = os.path.join(destination, marker)
                    if (os.path.islink(destination) or not os.path.isfile(proof)
                            or os.path.islink(proof)):
                        raise web.HTTPConflict(text='That folder already exists. Choose another folder name.')
                    with open(proof, encoding='utf-8') as f:
                        if f.read() != operation_id:
                            raise web.HTTPConflict(text='That folder belongs to another creation request.')
                else:
                    proof = os.path.join(temporary, marker)
                    if os.path.islink(temporary) or os.path.islink(proof):
                        raise web.HTTPConflict(text='The temporary project folder has changed.')
                    with open(proof, encoding='utf-8') as f:
                        if f.read() != operation_id:
                            raise web.HTTPConflict(text='Unable to verify the new project folder.')
                    os.rename(temporary, destination)
                project = dict(project)
                project['publish'] = sanitize_publish_path(project.get('publish'), destination)
                projects.append(project)
                atomic_projects(os.path.join(ctx.get_user_path(user), 'projects', 'projects.json'), projects)
                os.unlink(os.path.join(destination, marker))
                notify_sidebar()
                return {'project': project, 'projects': projects}

        def resolve_explorer_workspace(self, project_id, user=None, is_admin=False):
            if project_id is not None:
                return self.resolve_workspace(project_id, user)
            allowed = explorer_directories.get(user or "default", ())
            if not allowed and is_admin:
                allowed = explorer_directories["default"]
            if not allowed:
                # No central workspace: a user without startup directories browses their own
                # private one; only admins fall back to the server's startup directories.
                allowed = (user_workspace(user),)
            directories = [ctx.resolve_directory(path) for path in allowed]
            return {"projectId": None, "directories": [path for path in directories if path is not None]}

        def resolve_workspace(self, project_id, user=None):
            if project_id is None:
                return {"projectId": None, "directories": []}
            project = next((p for p in get_user_projects(user) if p["id"] == project_id), None)
            if not project:
                raise ValueError("Project not found")
            directory = os.path.realpath(get_project_dir(user, project))
            root = os.path.realpath(os.path.join(ctx.get_user_path(user), "projects"))
            if os.path.commonpath([root, directory]) != root:
                raise ValueError("Project folder must be inside the projects directory")
            return {"projectId": project_id, "directories": [directory]}

        def get_user_projects(self, user: Optional[str] = None):
            return get_user_projects(user)

    ctx.projects = ProjectsApi()
    from .creation import install_creation
    install_creation(ctx)
    from .explorer import install_explorer
    install_explorer(ctx)


__install__ = install
