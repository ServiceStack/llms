import datetime
import io
import json
import mimetypes
import os
import re
import tarfile
from typing import Optional

import aiohttp
from aiohttp import web

# DEFAULT_PUBLISH_BASE_URL = "https://localhost:5001"
DEFAULT_PUBLISH_BASE_URL = "https://ai.llmspy.org"
DEFAULT_REGISTER_PATH = "/embed/register.html?domain=llmspy.org"
DEFAULT_PUBLISH_THREAD_PATH = "/publish/thread"
DEFAULT_PUBLISH_MEDIA_PATH = "/publish/media"
DEFAULT_PUBLISH_PROJECT_PATH = "/publish/project/{name}"
DEFAULT_PUBLISH_AVATARS_PATH = "/publish/avatar/{profile}"
DEFAULT_PUBLISH_TO_CACHE_PATH = "/publish/cache"


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


def kebab_case(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-").lower()


def install(ctx):

    class PublishUrls:
        def __init__(self, config):
            self.base_url = config.get("baseUrl", DEFAULT_PUBLISH_BASE_URL)
            self.register_url = f"{self.base_url}{DEFAULT_REGISTER_PATH}"
            self.publish_thread_url = f"{self.base_url}{DEFAULT_PUBLISH_THREAD_PATH}"
            self.publish_media_url = f"{self.base_url}{DEFAULT_PUBLISH_MEDIA_PATH}"
            self.publish_project_url = f"{self.base_url}{DEFAULT_PUBLISH_PROJECT_PATH}"
            self.publish_avatars_url = f"{self.base_url}{DEFAULT_PUBLISH_AVATARS_PATH}"
            self.publish_to_cache_url = f"{self.base_url}{DEFAULT_PUBLISH_TO_CACHE_PATH}"

        def get_avatar_url(self, profile):
            return self.publish_avatars_url.format(profile=profile)

        def get_project_url(self, name):
            return self.publish_project_url.format(name=name)

    # helper to get user or default prompts
    def get_publish_config(user=None, obscure=True):
        candidate_paths = []
        if user:
            candidate_paths.append(os.path.join(ctx.get_user_path(user), "publish", "config.json"))
        candidate_paths.append(os.path.join(ctx.get_user_path(), "publish", "config.json"))

        obj = {"apiKey": None, "userName": None, "userId": None}
        for path in candidate_paths:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    txt = f.read()
                    obj = json.loads(txt)
                    if obscure and "apiKey" in obj and obj["apiKey"]:
                        obj["apiKey"] = obj["apiKey"][:3] + "******" + obj["apiKey"][-4:]

        publish_base_url = obj.get("baseUrl", DEFAULT_PUBLISH_BASE_URL)

        if "registerUrl" not in obj:
            obj["registerUrl"] = publish_base_url + DEFAULT_REGISTER_PATH
        return obj

    def save_config(user, config):
        config_path = os.path.join(ctx.get_user_path(user=user), "publish", "config.json")
        ctx.dbg(f"Saving publish config to: {config_path}")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

    async def handle_publish_config(request):
        return web.json_response(get_publish_config(user=ctx.get_username(request)))

    ctx.add_get("config.json", handle_publish_config)

    async def delete_config(request):
        user = ctx.get_username(request)
        config_path = os.path.join(ctx.get_user_path(user=user), "publish", "config.json")
        if os.path.exists(config_path):
            os.remove(config_path)
        return web.json_response(get_publish_config(user=user))

    ctx.add_post("disconnect", delete_config)

    async def save_publish_config(request):
        user = ctx.get_username(request)
        body = await request.json()
        existing_config = get_publish_config(user=user, obscure=False)
        if existing_config:
            if "apiKey" not in body or not body["apiKey"]:
                body["apiKey"] = existing_config.get("apiKey")
            existing_config.update(body)
            save_config(user, existing_config)
        else:
            save_config(user, body)
        return web.json_response(get_publish_config(user=user))

    ctx.add_post("config.json", save_publish_config)

    async def detect_dist(request):
        user = ctx.get_username(request)
        active_project = ctx.get_user_pref("project", user=user)
        user_projects = ctx.projects.get_user_projects(user) if hasattr(ctx, "projects") else []
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

        if not resolved_path.startswith(project_dir) or not os.path.exists(resolved_path) or not os.path.isdir(resolved_path):
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
                if parent_abs.startswith(project_dir):
                    rel_parent = os.path.relpath(parent_abs, project_dir)
                    parent_path = "" if rel_parent == "." else rel_parent

            user_projects_dir = os.path.abspath(os.path.join(ctx.get_user_path(user), "projects"))
            if resolved_path.startswith(user_projects_dir):
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

    async def get_publish_thread(request):
        thread_id = request.match_info["id"]
        thread = ctx.threads.get_thread(thread_id, user=ctx.get_username(request))
        if not thread:
            raise Exception(f"Thread {thread_id} not found")
        return web.json_response(thread)

    ctx.add_get("thread/{id}", get_publish_thread)

    async def publish_thread(request):
        user = ctx.get_username(request)
        config = get_publish_config(user=user, obscure=False)
        thread_id = request.match_info["id"]
        thread = ctx.threads.get_thread(thread_id, user=user)
        if not thread:
            raise Exception("Thread not found")

        urls = PublishUrls(config)
        publish_api_key = config.get("apiKey")
        metadata = thread.get("metadata", {})
        profile = metadata.get("profile", "default")

        if not publish_api_key:
            raise Exception("No API key configured")

        # Extract and upload dependent cache files
        ssl = False if urls.publish_thread_url.startswith("https://localhost:5001") else None

        # Find cache paths in the thread DTO
        cache_pattern = re.compile(r"/~cache/([^\s\)\"\'\>,]+)")

        def extract_cache_paths(obj):
            found = set()

            def scan(val):
                if isinstance(val, str):
                    for match in cache_pattern.finditer(val):
                        found.add((match.group(0), match.group(1)))
                elif isinstance(val, dict):
                    for v in val.values():
                        scan(v)
                elif isinstance(val, list):
                    for item in val:
                        scan(item)

            scan(obj)
            return found

        cache_references = extract_cache_paths(thread)

        if cache_references:
            cache_references = sorted(cache_references, key=lambda x: len(x[0]), reverse=True)
            async with aiohttp.ClientSession() as upload_session:
                upload_headers = {"Authorization": f"Bearer {publish_api_key}", "Accept": "application/json"}
                for orig_url, tail in cache_references:
                    file_path = ctx.get_cache_path(tail)
                    if os.path.exists(file_path):
                        # Upload main file
                        content_type, _ = mimetypes.guess_type(file_path)
                        if not content_type:
                            content_type = "application/octet-stream"

                        filename = os.path.basename(file_path)

                        data_form = aiohttp.FormData()
                        with open(file_path, "rb") as f:
                            file_bytes = f.read()
                        data_form.add_field("file", file_bytes, filename=filename, content_type=content_type)

                        file_path_no_ext = os.path.splitext(file_path)[0]
                        media = {}

                        # Check for sidecar .info file
                        sidecar_path = file_path_no_ext + ".info.json"
                        if os.path.exists(sidecar_path):
                            with open(sidecar_path, "rb") as f_sidecar:
                                media = json.loads(f_sidecar.read())

                        hash = filename.rsplit(".", 1)[0]
                        medias = ctx.media.query_media({"hash": hash}, user=user)
                        ctx.dbg(f"Found media {hash}: {len(medias)}")
                        if len(medias) > 0:
                            media.update(medias[0])

                        if "type" not in media:
                            continue

                        if "/" in media.get("type"):
                            media["type"] = media["type"].split("/")[0]

                        media_json = json.dumps(media)
                        media_bytes = media_json.encode()
                        data_form.add_field(
                            "info",
                            media_bytes,
                            filename=os.path.basename(sidecar_path),
                            content_type="application/json",
                        )

                        ctx.dbg(f"Uploading cache file {file_path} to {urls.publish_to_cache_url}")
                        try:
                            async with upload_session.post(
                                urls.publish_to_cache_url, headers=upload_headers, data=data_form, ssl=ssl
                            ) as upload_resp:
                                if upload_resp.status == 200:
                                    upload_text = await upload_resp.text()
                                    ctx.log(f"Cache upload response for {os.path.basename(file_path)}: {upload_text}")
                                else:
                                    ctx.err(
                                        f"Failed to upload cache file {file_path}, status: {upload_resp.status}", None
                                    )
                        except Exception as upload_err:
                            ctx.err(f"Exception during cache file upload {file_path}", upload_err)

        ctx.log(f"Publishing thread to {urls.publish_thread_url}")
        ctx.log(json.dumps(thread, indent=2))

        headers = {"Authorization": f"Bearer {publish_api_key}", "Content-Type": "application/json"}

        ssl = False if urls.publish_thread_url.startswith("https://localhost:5001") else None
        ctx.dbg(f"Publishing thread {thread_id} '{thread.get('title')}' to {urls.publish_thread_url}")
        async with aiohttp.ClientSession() as session, session.post(
            urls.publish_thread_url, headers=headers, json=thread, ssl=ssl
        ) as resp:
            text = await resp.text()
            status_code = getattr(resp, "status", 200)
            ctx.log(f"Thread {thread_id} published with status {status_code}")
            try:
                data = json.loads(text)
                now = datetime.datetime.now()
                data["publishedAt"] = now.isoformat()
                await ctx.threads.db.update_thread_async(
                    thread_id,
                    {"publishedAt": now, "publishedUrl": data.get("publishedUrl")},
                    user=user,
                )

                avatars = config.get("avatars")
                if avatars is None:
                    avatars = config["avatars"] = {}

                upload_avatars = []
                if "user" not in avatars:
                    publish_avatars_path = urls.get_avatar_url("user")
                    user_avatar_path = ctx.get_user_avatar_path(user)
                    if user_avatar_path is not None:
                        upload_avatars.append(("user", publish_avatars_path, user_avatar_path))
                if profile not in avatars:
                    publish_avatars_path = urls.get_avatar_url(profile)
                    user_avatar_path = ctx.get_profile_avatar_path(user, profile)
                    if user_avatar_path is not None:
                        upload_avatars.append((profile, publish_avatars_path, user_avatar_path))

                for upload_avatar in upload_avatars:
                    (profile, publish_avatar_url, avatar_path) = upload_avatar
                    # upload image to publishAvatarsPath
                    # save response { "publishedUrl": "url" } to avatars["user"]
                    content_type, _ = mimetypes.guess_type(avatar_path)
                    if not content_type:
                        content_type = "application/octet-stream"

                    data_form = aiohttp.FormData()
                    with open(avatar_path, "rb") as f:
                        file_bytes = f.read()

                    data_form.add_field(
                        "file",
                        file_bytes,
                        filename=os.path.basename(avatar_path),
                        content_type=content_type,
                    )

                    avatar_headers = {"Authorization": f"Bearer {publish_api_key}", "Accept": "application/json"}

                    try:
                        ctx.dbg(f"Publishing avatar {profile} from {avatar_path} to {publish_avatar_url}")
                        async with aiohttp.ClientSession() as session, session.post(
                            publish_avatar_url, headers=avatar_headers, data=data_form, ssl=ssl
                        ) as avatar_resp:
                            if avatar_resp.status == 200:
                                avatar_text = await avatar_resp.text()
                                ctx.dbg(avatar_text)
                                avatar_data = json.loads(avatar_text)
                                if "publishedUrl" in avatar_data:
                                    avatars[profile] = avatar_data["publishedUrl"]
                                    # save modified config to config.json
                                    save_config(user, config)
                    except Exception as e:
                        ctx.err(f"Failed to upload user avatar to {publish_avatars_path}", e)

                return web.json_response(data, status=status_code)
            except json.JSONDecodeError:
                content_type = getattr(resp, "content_type", "text/plain")
                return web.Response(text=text, status=status_code, content_type=content_type)

    ctx.add_post("thread/{id}", publish_thread)

    async def publish_project(request):
        user = ctx.get_username(request)
        name = request.match_info["name"]
        user_projects = ctx.projects.get_user_projects(user)
        projects = [p for p in user_projects if p["name"] == name]
        if len(projects) == 0:
            raise Exception("Project not found")
        project = projects[0]

        config = get_publish_config(user=user, obscure=False)
        urls = PublishUrls(config)
        publish_project_url = urls.get_project_url(name)

        publish_api_key = config.get("apiKey")
        if not publish_api_key:
            raise Exception("No API key configured")

        folder = project.get("folder") or kebab_case(project.get("name", ""))
        project_dir = os.path.abspath(os.path.join(ctx.get_user_path(user), "projects", folder))

        if project.get("publish") is None:
            raise Exception("No publish directory configured for the project")

        publish_dir = sanitize_publish_path(project.get("publish"), project_dir)
        resolved_publish_dir = os.path.abspath(os.path.join(project_dir, publish_dir))

        if not resolved_publish_dir.startswith(project_dir):
            raise Exception("Publish directory must be within the project folder")

        if (
            not resolved_publish_dir
            or not os.path.exists(resolved_publish_dir)
            or not os.path.isdir(resolved_publish_dir)
        ):
            raise Exception(f"Publish directory does not exist: {publish_dir or 'project root'}")

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w:gz") as tar:
            for root, dirs, files in os.walk(resolved_publish_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, resolved_publish_dir)
                    tar.add(full_path, arcname=rel_path)
                for d in dirs:
                    full_path = os.path.join(root, d)
                    rel_path = os.path.relpath(full_path, resolved_publish_dir)
                    if not os.listdir(full_path):
                        tar.add(full_path, arcname=rel_path)

        tar_bytes = tar_stream.getvalue()

        data_form = aiohttp.FormData()
        data_form.add_field(
            "info",
            json.dumps(project).encode("utf-8"),
            filename="info.json",
            content_type="application/json",
        )
        data_form.add_field(
            "file",
            tar_bytes,
            filename=f"{name}.tar.gz",
            content_type="application/gzip",
        )

        headers = {"Authorization": f"Bearer {publish_api_key}", "Accept": "application/json"}
        ssl = False if publish_project_url.startswith("https://localhost:5001") else None

        ctx.dbg(f"Publishing project {name} from {resolved_publish_dir} to {publish_project_url}")
        async with aiohttp.ClientSession() as session, session.post(
            publish_project_url, headers=headers, data=data_form, ssl=ssl
        ) as resp:
            text = await resp.text()
            status_code = getattr(resp, "status", 200)
            try:
                data = json.loads(text)
                if status_code == 200 and "publishedUrl" in data:
                    projects_list = ctx.projects.get_user_projects(user)
                    updated = False
                    for proj in projects_list:
                        if proj.get("name") == name:
                            proj["publishedUrl"] = data["publishedUrl"]
                            updated = True
                            break
                    if updated:
                        if user:
                            write_path = os.path.join(ctx.get_user_path(user), "projects", "projects.json")
                        else:
                            write_path = os.path.join(ctx.get_user_path(), "projects", "projects.json")
                        os.makedirs(os.path.dirname(write_path), exist_ok=True)
                        with open(write_path, "w", encoding="utf-8") as f:
                            json.dump(projects_list, f, indent=2, ensure_ascii=False)
                return web.json_response(data, status=status_code)
            except json.JSONDecodeError:
                content_type = getattr(resp, "content_type", "text/plain")
                return web.Response(text=text, status=status_code, content_type=content_type)

    ctx.add_post("project/{name}", publish_project)


    async def publish_media(request):
        user = ctx.get_username(request)
        id = request.match_info["id"]

        rows = ctx.media.query_media({"id": id}, user)
        if not rows:
            return web.json_response({"error": "Media not found"}, status=404)
        media = rows[0]

        config = get_publish_config(user=user, obscure=False)
        urls = PublishUrls(config)

        publish_api_key = config.get("apiKey")
        if not publish_api_key:
            raise Exception("No API key configured")

        media_url = media.get("url")
        if not media_url:
            raise Exception("Media URL not found")

        if not media_url.startswith("/~cache/"):
            raise Exception("Invalid cache URL format")

        cache_tail = media_url[len("/~cache/"):]
        file_path = ctx.get_cache_path(cache_tail)

        if not os.path.exists(file_path):
            return web.json_response({"error": f"Cached file not found: {file_path}"}, status=404)

        content_type, _ = mimetypes.guess_type(file_path)
        if not content_type:
            content_type = "application/octet-stream"

        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        data_form = aiohttp.FormData()
        data_form.add_field(
            "info",
            json.dumps(media).encode("utf-8"),
            filename="info.json",
            content_type="application/json",
        )
        data_form.add_field(
            "file",
            file_bytes,
            filename=filename,
            content_type=content_type,
        )

        headers = {"Authorization": f"Bearer {publish_api_key}", "Accept": "application/json"}
        ssl = False if urls.publish_media_url.startswith("https://localhost:5001") else None

        ctx.dbg(f"Publishing media {id} from {file_path} to {urls.publish_media_url}")
        async with aiohttp.ClientSession() as session, session.post(
            urls.publish_media_url, headers=headers, data=data_form, ssl=ssl
        ) as resp:
            text = await resp.text()
            status_code = getattr(resp, "status", 200)
            try:
                data = json.loads(text)

                now = datetime.datetime.now()
                data["publishedAt"] = now.isoformat()
                await ctx.media.update_media_async(
                    id,
                    {"publishedAt": now, "publishedUrl": data.get("publishedUrl")},
                    user=user,
                )

                return web.json_response(data, status=status_code)
            except json.JSONDecodeError:
                content_type = getattr(resp, "content_type", "text/plain")
                return web.Response(text=text, status=status_code, content_type=content_type)

    ctx.add_post("media/{id}", publish_media)

__install__ = install
