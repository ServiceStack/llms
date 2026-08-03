import glob
import hashlib
import json
import os
import re
import shutil
import time

from aiohttp import web


def get_profile_color(name):
    colors = [
        "#3b82f6", "#10b981", "#8b5cf6", "#f59e0b", "#ef4444",
        "#ec4899", "#06b6d4", "#6366f1", "#14b8a6", "#f97316"
    ]
    hash_val = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
    return colors[hash_val % len(colors)]


def install(ctx):
    builtin_profiles_dir = os.path.join(os.path.dirname(__file__), "profiles")

    def is_builtin_profile(profile_name):
        p = os.path.join(builtin_profiles_dir, profile_name)
        return os.path.exists(p) and os.path.isdir(p)

    def get_user_profile_dir(req, profile_name):
        user = ctx.get_username(req)
        user_path = ctx.get_user_path(user=user) if user else ctx.get_user_path()
        return os.path.join(user_path, "profiles", profile_name)

    def get_profile_path(req):
        profile = req.match_info["profile"]
        user = ctx.get_username(req)
        all_paths = [builtin_profiles_dir, os.path.join(ctx.get_user_path(), "profiles")]
        if user:
            all_paths.append(os.path.join(ctx.get_user_path(user=user), "profiles"))

        for profiles_path in reversed(all_paths):
            profile_path = os.path.join(profiles_path, profile)
            if os.path.exists(profile_path):
                return profile_path

        return None

    def get_profile_files_list(profile_path):
        if not profile_path or not os.path.exists(profile_path):
            return []
        files = []
        for filename in os.listdir(profile_path):
            if (filename.endswith(".md") or filename == "SYSTEM.template") and os.path.isfile(os.path.join(profile_path, filename)):
                files.append(filename)

        def file_key(f):
            if f == "SYSTEM.template":
                return (0, f)
            if f == "SYSTEM.md":
                return (1, f)
            return (2, f.lower())

        return sorted(files, key=file_key)

    async def get_profiles(req):
        user = ctx.get_username(req)
        builtin_path = builtin_profiles_dir
        user_paths = [os.path.join(ctx.get_user_path(), "profiles")]
        if user:
            user_paths.append(os.path.join(ctx.get_user_path(user=user), "profiles"))

        all_paths = [builtin_path] + user_paths

        ret = {}
        for profiles_path in all_paths:
            is_builtin = (profiles_path == builtin_path)
            if os.path.exists(profiles_path):
                for f in os.listdir(profiles_path):
                    profile_dir = os.path.join(profiles_path, f)
                    if os.path.isdir(profile_dir):
                        agent_json_path = os.path.join(profile_dir, "config.json")
                        if os.path.exists(agent_json_path):
                            try:
                                with open(agent_json_path, encoding="utf-8") as fh:
                                    obj = json.load(fh)
                                    if "enabled" in obj and not obj["enabled"]:
                                        ret.pop(f, None)
                                        continue
                                    obj["isBuiltIn"] = is_builtin and not os.path.exists(os.path.join(user_paths[-1], f))
                                    obj["files"] = get_profile_files_list(profile_dir)
                                    ret[f] = obj
                                    ctx.dbg(f"{f} profile loaded from {profile_dir}")
                            except Exception:
                                pass

        return web.json_response(ret)

    ctx.add_get("", get_profiles)

    async def create_profile(req):
        data = await req.json()
        name = data.get("name", "").strip()
        if not name:
            raise web.HTTPBadRequest(text="Profile name is required")

        profile_id = re.sub(r'[^a-zA-Z0-9]+', '-', name.lower()).strip('-')
        if not profile_id:
            profile_id = f"agent-{int(time.time())}"

        user_profile_dir = get_user_profile_dir(req, profile_id)
        if os.path.exists(user_profile_dir) or is_builtin_profile(profile_id):
            raise web.HTTPBadRequest(text=f"Profile '{profile_id}' already exists")

        os.makedirs(user_profile_dir, exist_ok=True)

        config = {
            "name": name,
            "model": None,
            "theme": None,
            "onlySkills": None,
            "onlyTools": None
        }

        with open(os.path.join(user_profile_dir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=4)

        with open(os.path.join(user_profile_dir, "SYSTEM.md"), "w", encoding="utf-8") as fh:
            fh.write("")

        return web.json_response({
            "status": "ok",
            "id": profile_id,
            "name": name,
            "config": config
        })

    ctx.add_post("", create_profile)

    async def delete_profile(req):
        profile = req.match_info["profile"]
        user_profile_dir = get_user_profile_dir(req, profile)

        if profile == "default" or (is_builtin_profile(profile) and not os.path.exists(user_profile_dir)):
            raise web.HTTPForbidden(text="Built-in profiles cannot be deleted")

        if not os.path.exists(user_profile_dir):
            raise web.HTTPNotFound(text="Profile not found")

        try:
            shutil.rmtree(user_profile_dir)
        except Exception as e:
            raise Exception(f"Failed to delete profile: {e}")

        return web.json_response({"status": "ok", "id": profile})

    ctx.add_delete("{profile}", delete_profile)

    async def get_tools_and_skills(req):
        tools = sorted(list(ctx.app.tools.keys()))

        user = ctx.get_username(req)
        user_skills_dir = os.path.join(ctx.get_user_path(user=user), "skills") if user else os.path.join(ctx.get_user_path(), "skills")
        global_skills_dir = os.path.expanduser("~/.llms/.agent/skills")
        workspace_skills_dir = os.path.join(os.getcwd(), ".agent", "skills")

        skill_names = set()
        for sdir in [global_skills_dir, workspace_skills_dir, user_skills_dir]:
            if os.path.exists(sdir):
                for sk in os.listdir(sdir):
                    if os.path.isdir(os.path.join(sdir, sk)):
                        skill_names.add(sk)

        if "skills" in ctx.app.tool_groups:
            for sk in ctx.app.tool_groups["skills"]:
                skill_names.add(sk)

        skills = sorted(list(skill_names))
        return web.json_response({"tools": tools, "skills": skills})

    ctx.add_get("tools-skills", get_tools_and_skills)

    async def update_profile_config(req):
        profile = req.match_info["profile"]
        user_profile_dir = get_user_profile_dir(req, profile)

        if is_builtin_profile(profile) and not os.path.exists(user_profile_dir):
            raise web.HTTPForbidden(text="Built-in profiles are read-only")

        data = await req.json()
        os.makedirs(user_profile_dir, exist_ok=True)
        config_path = os.path.join(user_profile_dir, "config.json")

        existing_config = {}
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as fh:
                try:
                    existing_config = json.load(fh)
                except Exception:
                    pass

        if "name" in data:
            existing_config["name"] = data["name"]
        if "model" in data:
            existing_config["model"] = data["model"]
        if "theme" in data:
            existing_config["theme"] = data["theme"]
        if "onlyTools" in data:
            existing_config["onlyTools"] = data["onlyTools"]
        if "onlySkills" in data:
            existing_config["onlySkills"] = data["onlySkills"]

        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(existing_config, fh, indent=4)

        return web.json_response(existing_config)

    ctx.add_post("{profile}/config", update_profile_config)

    async def list_files(req):
        profile_path = get_profile_path(req)
        if not profile_path:
            raise web.HTTPNotFound(text="Profile not found")
        files = get_profile_files_list(profile_path)
        return web.json_response(files)

    ctx.add_get("{profile}/files", list_files)

    async def get_file_content(req):
        profile_path = get_profile_path(req)
        if not profile_path:
            raise web.HTTPNotFound(text="Profile not found")
        filename = req.match_info["filename"]

        filename = os.path.basename(filename)
        if not (filename.endswith(".md") or filename == "SYSTEM.template"):
            raise web.HTTPBadRequest(text="Invalid file type")

        file_path = os.path.join(profile_path, filename)
        if not os.path.exists(file_path):
            raise web.HTTPNotFound(text="File not found")

        with open(file_path, encoding="utf-8") as fh:
            content = fh.read()
        return web.Response(text=content, content_type="text/plain")

    ctx.add_get("{profile}/files/{filename}", get_file_content)

    async def save_file_content(req):
        profile = req.match_info["profile"]
        user_profile_dir = get_user_profile_dir(req, profile)

        if is_builtin_profile(profile) and not os.path.exists(user_profile_dir):
            raise web.HTTPForbidden(text="Built-in profiles are read-only")

        filename = req.match_info["filename"]
        filename = os.path.basename(filename)
        if not (filename.endswith(".md") or filename == "SYSTEM.template"):
            raise web.HTTPBadRequest(text="Invalid file type")

        os.makedirs(user_profile_dir, exist_ok=True)
        content = await req.text()
        file_path = os.path.join(user_profile_dir, filename)

        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(content)

        return web.json_response({"status": "ok", "filename": filename})

    ctx.add_put("{profile}/files/{filename}", save_file_content)

    async def create_file(req):
        profile = req.match_info["profile"]
        user_profile_dir = get_user_profile_dir(req, profile)

        if is_builtin_profile(profile) and not os.path.exists(user_profile_dir):
            raise web.HTTPForbidden(text="Built-in profiles are read-only")

        data = await req.json()
        filename = data.get("filename", "").strip()
        filename = os.path.basename(filename)

        os.makedirs(user_profile_dir, exist_ok=True)
        system_template_path = os.path.join(user_profile_dir, "SYSTEM.template")
        system_md_path = os.path.join(user_profile_dir, "SYSTEM.md")

        if filename == "SYSTEM.template" or filename == "SYSTEM.template.md":
            filename = "SYSTEM.template"
            if os.path.exists(system_md_path):
                os.rename(system_md_path, system_template_path)
            elif not os.path.exists(system_template_path):
                content = data.get("content", "")
                with open(system_template_path, "w", encoding="utf-8") as fh:
                    fh.write(content)
        elif filename == "SYSTEM.md":
            filename = "SYSTEM.md"
            if os.path.exists(system_template_path):
                os.rename(system_template_path, system_md_path)
            elif not os.path.exists(system_md_path):
                content = data.get("content", "")
                with open(system_md_path, "w", encoding="utf-8") as fh:
                    fh.write(content)
        else:
            if not filename.endswith(".md"):
                filename += ".md"
            content = data.get("content", "")
            file_path = os.path.join(user_profile_dir, filename)
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write(content)

        return web.json_response({"status": "ok", "filename": filename})

    ctx.add_post("{profile}/files", create_file)

    async def delete_file(req):
        profile = req.match_info["profile"]
        user_profile_dir = get_user_profile_dir(req, profile)

        if is_builtin_profile(profile) and not os.path.exists(user_profile_dir):
            raise web.HTTPForbidden(text="Built-in profiles are read-only")

        filename = req.match_info["filename"]
        filename = os.path.basename(filename)
        if not filename.endswith(".md"):
            raise web.HTTPBadRequest(text="Can only delete .md files")

        file_path = os.path.join(user_profile_dir, filename)
        if os.path.exists(file_path):
            os.remove(file_path)

        return web.json_response({"status": "ok", "filename": filename})

    ctx.add_delete("{profile}/files/{filename}", delete_file)

    async def upload_avatar(req):
        profile = req.match_info["profile"]
        user_profile_dir = get_user_profile_dir(req, profile)

        if is_builtin_profile(profile) and not os.path.exists(user_profile_dir):
            raise web.HTTPForbidden(text="Built-in profiles are read-only")

        os.makedirs(user_profile_dir, exist_ok=True)

        ext = "png"
        content_type = req.headers.get("Content-Type", "")

        if req.content_type == "multipart/form-data":
            reader = await req.multipart()
            field = await reader.next()
            filename = field.filename or "avatar.png"
            ext = os.path.splitext(filename)[1].lstrip(".").lower() or "png"
            if ext not in ["png", "webp", "jpg", "jpeg", "svg"]:
                ext = "png"
            avatar_data = await field.read()
        else:
            if "webp" in content_type:
                ext = "webp"
            elif "jpeg" in content_type or "jpg" in content_type:
                ext = "jpg"
            elif "svg" in content_type:
                ext = "svg"
            avatar_data = await req.read()

        for ex in ["png", "webp", "jpg", "jpeg", "svg"]:
            old_p = os.path.join(user_profile_dir, f"avatar.{ex}")
            if os.path.exists(old_p):
                try:
                    os.remove(old_p)
                except Exception:
                    pass

        avatar_path = os.path.join(user_profile_dir, f"avatar.{ext}")
        with open(avatar_path, "wb") as fh:
            fh.write(avatar_data)

        return web.json_response({"status": "ok", "avatar": f"avatar.{ext}"})

    ctx.add_post("{profile}/avatar", upload_avatar)

    async def get_profile_prompt(req):
        profile_path = get_profile_path(req)
        if not profile_path:
            raise Exception("Profile not found")
        system_template_path = os.path.join(profile_path, "SYSTEM.template")
        if os.path.exists(system_template_path):
            with open(system_template_path, encoding="utf-8") as f:
                system_template = f.read()

            template_variables = {}
            for filename in os.listdir(profile_path):
                if filename.endswith(".md"):
                    key = filename[:-3]
                    with open(os.path.join(profile_path, filename), encoding="utf-8") as fh:
                        template_variables[key] = fh.read()

            memory_path = os.path.join(profile_path, "memory")
            if os.path.isdir(memory_path):
                memory_files = sorted(
                    [f for f in os.listdir(memory_path) if f.endswith(".md")],
                    reverse=True,
                )
                if memory_files:
                    latest_file = os.path.join(memory_path, memory_files[0])
                    with open(latest_file, encoding="utf-8") as fh:
                        template_variables["MEMORY_LATEST"] = fh.read()
            if "MEMORY_LATEST" not in template_variables:
                template_variables["MEMORY_LATEST"] = ""

            render_template = system_template.format(**template_variables)
            return web.Response(text=render_template, content_type="text/plain")

        system_md_path = os.path.join(profile_path, "SYSTEM.md")
        if os.path.exists(system_md_path):
            with open(system_md_path, encoding="utf-8") as f:
                return web.Response(text=f.read(), content_type="text/plain")

        raise Exception("SYSTEM.md or SYSTEM.template not found")

    ctx.add_get("{profile}/system", get_profile_prompt)

    async def get_avatar(req):
        profile = req.match_info.get("profile", "")
        profile_path = get_profile_path(req) or ""

        headers = {"Cache-Control": "no-cache", "Content-Type": "image/svg+xml"}

        exts = {
            "png": "image/png",
            "webp": "image/webp",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "svg": "image/svg+xml",
        }

        if profile_path:
            for ext, ct in exts.items():
                p = os.path.join(profile_path, f"avatar.{ext}")
                if os.path.exists(p):
                    headers["Content-Type"] = ct
                    return web.FileResponse(p, headers=headers)

        profile_name = profile
        if profile_path:
            config_path = os.path.join(profile_path, "config.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, encoding="utf-8") as fh:
                        cfg = json.load(fh)
                        profile_name = cfg.get("name", profile)
                except Exception:
                    pass

        initial = (profile_name or profile or "A").strip()[0].upper()
        bg_color = get_profile_color(profile_name or profile)

        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
            <circle cx="32" cy="32" r="32" fill="{bg_color}"/>
            <text x="32" y="42" font-size="30" font-family="system-ui, -apple-system, sans-serif" font-weight="bold" fill="#ffffff" text-anchor="middle">{initial}</text>
        </svg>"""
        return web.Response(text=svg, headers=headers)

    ctx.add_get("{profile}/avatar", get_avatar)

    async def get_profile_actions(user, profile_path):
        config_path = os.path.join(profile_path, "config.json")
        if not os.path.exists(config_path):
            return {}

        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)

        actions = config.get("actions", {})

        def glob_exists(match):
            allowed_dirs = ctx.resolve_allowed_directories(user=user)
            for dir in allowed_dirs:
                pattern = os.path.join(dir, match)
                if os.path.exists(pattern):
                    return True
                else:
                    if match.endswith("/"):
                        match = match[:-1]
                    files = glob.glob(pattern)
                    if files:
                        return True
            return False

        valid_actions = {}
        for name, act in actions.items():
            condition = act.get("condition", {})
            type = condition.get("type", "")
            match = condition.get("glob", "")
            if not condition or not type or not match:
                valid_actions[name] = act
                continue

            exists = condition.get("exists", False)

            if type == "file":
                file_exists = glob_exists(match)
                if (exists and file_exists) or (not exists and not file_exists):
                    valid_actions[name] = act
            else:
                ctx.log(f"Unknown condition type: {condition}")
        return valid_actions

    async def get_actions(req):
        user = ctx.get_username(req)
        profile_path = get_profile_path(req) or ""
        valid_actions = await get_profile_actions(user, profile_path)
        return web.json_response(valid_actions)

    ctx.add_get("{profile}/actions", get_actions)


__install__ = install
