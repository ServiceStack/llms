import datetime
import io
import json
import mimetypes
import os
import re
import tarfile

import aiohttp
from aiohttp import web

from llms.extensions.projects.publishing import (
    is_path_within,
    kebab_case,
    register_project_output_routes,
    sanitize_publish_path,
)

# DEFAULT_PUBLISH_BASE_URL = "https://localhost:5001"
DEFAULT_PUBLISH_BASE_URL = "https://ai.llmspy.org"
DEFAULT_REGISTER_PATH = "/embed/register.html?domain=llmspy.org"
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

        def get_avatar_url(self, profile):
            return self.publish_avatars_url.format(profile=profile)

        def get_project_url(self, name):
            return self.publish_project_url.format(name=name)

    from llms.extensions.share_llmspy.client import get_publish_config as account_publish_config

    def get_publish_config(user=None, obscure=True):
        return account_publish_config(ctx, user, obscure)

    def public_config(user):
        config = get_publish_config(user=user)
        return config

    ctx.app.publisher_available = True

    def save_config(user, config):
        config_path = os.path.join(ctx.get_user_path(user=user), "share_llmspy", "config.json")
        ctx.dbg(f"Saving publish config to: {config_path}")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        legacy = os.path.join(ctx.get_user_path(user=user), "publish", "config.json")
        if os.path.exists(legacy):
            os.remove(legacy)

    async def handle_publish_config(request):
        return web.json_response(public_config(ctx.get_username(request)))

    ctx.add_get("config.json", handle_publish_config)

    async def delete_config(request):
        user = ctx.get_username(request)
        config_path = os.path.join(ctx.get_user_path(user=user), "share_llmspy", "config.json")
        for path in (config_path, os.path.join(ctx.get_user_path(user=user), "publish", "config.json")):
            if os.path.exists(path):
                os.remove(path)
        return web.json_response(public_config(user))

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
        return web.json_response(public_config(user))

    ctx.add_post("config.json", save_publish_config)

    register_project_output_routes(ctx)

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
                for _orig_url, tail in cache_references:
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

        if not is_path_within(resolved_publish_dir, project_dir):
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

        info_project = {k: v for k, v in project.items() if k != "id"}
        data_form = aiohttp.FormData()
        data_form.add_field(
            "info",
            json.dumps(info_project).encode("utf-8"),
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
                    ctx.projects.update_publication(project['id'], {'publishedUrl': data['publishedUrl']}, user)
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
