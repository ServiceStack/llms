import datetime
import io
import json
import mimetypes
import os
import re
import tarfile

import aiohttp
from aiohttp import web

# DEFAULT_BASE_URL = "https://localhost:5001"
DEFAULT_REGISTER_PATH = "/embed/register.html?domain=llmspy.org"
DEFAULT_PUBLISH_BASE_URL = "https://ai.llmspy.org"
DEFAULT_PUBLISH_THREAD_PATH = "/publish/thread"
DEFAULT_PUBLISH_MEDIA_PATH = "/publish/media"
DEFAULT_PUBLISH_PROJECT_PATH = "/publish/project/{name}"
DEFAULT_PUBLISH_AVATARS_PATH = "/publish/avatar/{profile}"
DEFAULT_PUBLISH_TO_CACHE_PATH = "/publish/cache"


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

        def get_avatar_url(self, user):
            return self.publish_avatars_url.format(profile=user)

        def get_project_url(self, name):
            return self.publish_project_url.format(name=name)

    # helper to get user or default prompts
    def get_publish_config(user=None, obscure=True):
        candidate_paths = []
        if user:
            # if signed in, return the prompts for this user if exists
            candidate_paths.append(os.path.join(ctx.get_user_path(user), "publish", "config.json"))
        # return default prompts for all users if exists
        candidate_paths.append(os.path.join(ctx.get_user_path(), "publish", "config.json"))

        # iterate all candidate paths and when exists return its json
        obj = {"apiKey": None, "userName": None, "userId": None}
        for path in candidate_paths:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    txt = f.read()
                    obj = json.loads(txt)
                    if obscure and "apiKey" in obj and obj["apiKey"]:
                        # hide visible key first 3 and last 4 chars
                        obj["apiKey"] = obj["apiKey"][:3] + "******" + obj["apiKey"][-4:]

        publish_base_url = obj.get("baseUrl", DEFAULT_PUBLISH_BASE_URL)

        # used by client to load register form
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
        # check if user is signed in
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
        # read the request body
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

        candidate_paths = []
        if user:
            candidate_paths.append(os.path.join(ctx.get_user_path(user), "projects", "projects.json"))
        candidate_paths.append(os.path.join(ctx.get_user_path(), "projects", "projects.json"))

        project_paths = []
        publish_prop = None
        if active_project:
            for path in candidate_paths:
                if os.path.exists(path):
                    try:
                        with open(path, encoding="utf-8") as f:
                            projects = json.load(f)
                            for proj in projects:
                                if proj.get("name") == active_project:
                                    project_paths = proj.get("paths", [])
                                    publish_prop = proj.get("publish")
                                    break
                            if project_paths or publish_prop:
                                break
                    except Exception as e:
                        ctx.err("Failed to read projects in publish detect-dist", e)

        if publish_prop:
            resolved_publish = ctx.resolve_directory(publish_prop)
            if resolved_publish:
                return web.json_response({"dist": resolved_publish})
            return web.json_response({"dist": publish_prop})

        special_prefixes = ("$WORKSPACE", "$TEMP")
        custom_paths = [p for p in project_paths if not p.startswith(special_prefixes)]

        detected_dist = None
        first_custom_path = None
        for p in custom_paths:
            resolved = ctx.resolve_directory(p)
            if resolved:
                if first_custom_path is None:
                    first_custom_path = resolved
                dist_path = os.path.join(resolved, "dist")
                if os.path.exists(dist_path) and os.path.isdir(dist_path):
                    detected_dist = dist_path
                    break

        return web.json_response({"dist": detected_dist or first_custom_path or ""})

    ctx.add_get("detect-dist", detect_dist)

    async def list_subdirs(request):
        user = ctx.get_username(request)
        path_param = request.query.get("path", "")

        # If path is empty, default to active project paths or workspace root
        if not path_param:
            active_project = ctx.get_user_pref("project", user=user)
            project_paths = []
            if active_project:
                candidate_paths = []
                if user:
                    candidate_paths.append(os.path.join(ctx.get_user_path(user), "projects", "projects.json"))
                candidate_paths.append(os.path.join(ctx.get_user_path(), "projects", "projects.json"))
                for path in candidate_paths:
                    if os.path.exists(path):
                        try:
                            with open(path, encoding="utf-8") as f:
                                projects = json.load(f)
                                for proj in projects:
                                    if proj.get("name") == active_project:
                                        project_paths = proj.get("paths", [])
                                        break
                                if project_paths:
                                    break
                        except Exception:
                            pass
            if not project_paths:
                project_paths = ["$WORKSPACE"]
            path_param = project_paths[0]

        resolved_path = ctx.resolve_directory(path_param)
        if not resolved_path or not os.path.exists(resolved_path) or not os.path.isdir(resolved_path):
            return web.json_response({"error": "Invalid or non-existent path", "path": path_param}, status=400)

        try:
            subdirs = []
            for item in os.listdir(resolved_path):
                full_path = os.path.join(resolved_path, item)
                if os.path.isdir(full_path) and not item.startswith("."):
                    subdirs.append({"name": item, "path": full_path})
            subdirs.sort(key=lambda x: x["name"].lower())

            parent_path = os.path.dirname(resolved_path)
            is_parent_allowed = False
            allowed_dirs = ctx.resolve_allowed_directories(user)
            for allowed_dir in allowed_dirs:
                if parent_path.startswith(allowed_dir) or parent_path == allowed_dir:
                    is_parent_allowed = True
                    break

            return web.json_response(
                {
                    "currentPath": resolved_path,
                    "parentPath": parent_path if is_parent_allowed else None,
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

        publish_dir = project.get("publish")
        if not publish_dir:
            raise Exception("No publish directory configured for the project")

        resolved_publish_dir = ctx.resolve_directory(publish_dir)
        if (
            not resolved_publish_dir
            or not os.path.exists(resolved_publish_dir)
            or not os.path.isdir(resolved_publish_dir)
        ):
            raise Exception(f"Publish directory does not exist: {publish_dir}")

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
