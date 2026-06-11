import json
import os

from aiohttp import web


def install(ctx):
    def get_profile_path(req):
        profile = req.match_info["profile"]
        user = ctx.get_username(req)
        all_paths = [os.path.join(os.path.dirname(__file__), "profiles"), os.path.join(ctx.get_user_path(), "profiles")]
        if user:
            all_paths.append(os.path.join(ctx.get_user_path(user=user), "profiles"))

        for profiles_path in reversed(all_paths):
            profile_path = os.path.join(profiles_path, profile)
            if os.path.exists(profile_path):
                return profile_path

        return None

    async def get_profiles(req):
        user = ctx.get_username(req)
        all_paths = [os.path.join(os.path.dirname(__file__), "profiles"), os.path.join(ctx.get_user_path(), "profiles")]
        if user:
            all_paths.append(os.path.join(ctx.get_user_path(user=user), "profiles"))

        ret = {}
        for profiles_path in all_paths:
            if os.path.exists(profiles_path):
                for f in os.listdir(profiles_path):
                    profile_dir = os.path.join(profiles_path, f)
                    if os.path.isdir(profile_dir):
                        agent_json_path = os.path.join(profile_dir, "config.json")
                        if not os.path.exists(agent_json_path):
                            agent_json_path = os.path.join(profile_dir, "config.json")

                        if os.path.exists(agent_json_path):
                            try:
                                with open(agent_json_path, encoding="utf-8") as fh:
                                    ret[f] = obj = json.load(fh)

                                    # Default to enabled if not specified
                                    if "enabled" in obj and not obj["enabled"]:
                                        ret.pop(f)

                                    ctx.dbg(f"{f} profile loaded from {profile_dir}")
                            except Exception:
                                pass

        return web.json_response(ret)

    ctx.add_get("", get_profiles)

    async def get_profile_prompt(req):
        profile_path = get_profile_path(req)
        if not profile_path:
            raise Exception("Profile not found")
        system_template_path = os.path.join(profile_path, "SYSTEM.template")
        if os.path.exists(system_template_path):
            with open(system_template_path, encoding="utf-8") as f:
                system_template = f.read()

            # Read all .md files in folder, key is filename without extension
            template_variables = {}
            for filename in os.listdir(profile_path):
                if filename.endswith(".md"):
                    key = filename[:-3]  # Strip .md extension
                    with open(os.path.join(profile_path, filename), encoding="utf-8") as fh:
                        template_variables[key] = fh.read()

            # Handle MEMORY_LATEST: get the latest file from memory/ subdirectory
            memory_path = os.path.join(profile_path, "memory")
            if os.path.isdir(memory_path):
                memory_files = sorted(
                    [f for f in os.listdir(memory_path) if f.endswith(".md")],
                    reverse=True,  # Newest first (ISO dates sort correctly)
                )
                if memory_files:
                    latest_file = os.path.join(memory_path, memory_files[0])
                    with open(latest_file, encoding="utf-8") as fh:
                        template_variables["MEMORY_LATEST"] = fh.read()
            if "MEMORY_LATEST" not in template_variables:
                template_variables["MEMORY_LATEST"] = ""

            render_template = system_template.format(**template_variables)
            # return plain text
            return web.Response(text=render_template, content_type="text/plain")

        system_md_path = os.path.join(profile_path, "SYSTEM.md")
        if os.path.exists(system_md_path):
            with open(system_md_path, encoding="utf-8") as f:
                return web.Response(text=f.read(), content_type="text/plain")

        raise Exception("SYSTEM.md or SYSTEM.template not found")

    ctx.add_get("{profile}/system", get_profile_prompt)

    async def get_avatar(req):
        profile_path = get_profile_path(req) or ""

        # Cache for 1 hour
        headers = {"Cache-Control": "public, max-age=3600"}

        exts = {
            "png": "image/png",
            "webp": "image/webp",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "svg": "image/svg+xml",
        }

        for ext, ct in exts.items():
            p = os.path.join(profile_path, f"avatar.{ext}")
            if os.path.exists(p):
                headers["Content-Type"] = ct
                return web.FileResponse(p, headers=headers)

        # Fall back to extension's default avatar
        default_avatar = os.path.join(os.path.dirname(__file__), "ui", "avatar.svg")
        headers["Content-Type"] = "image/svg+xml"
        return web.FileResponse(default_avatar, headers=headers)

    ctx.add_get("{profile}/avatar", get_avatar)


__install__ = install
