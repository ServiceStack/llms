import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

import aiohttp

from .parser import read_properties

# Example of what's returned from https://skills.sh/api/skills?limit=5000&offset=0 > ui/data/skills-top-5000.json
# {
#  "id": "vercel-react-best-practices",
#  "name": "vercel-react-best-practices",
#  "installs": 68580,
#  "topSource": "vercel-labs/agent-skills"
# }
g_available_skills = []

LLMS_HOME_SKILLS = "~/.llms/.agent/skills"
LLMS_LOCAL_SKILLS = ".agent/skills"


def is_safe_path(base_path: str, requested_path: str) -> bool:
    """Check if the requested path is safely within the base path."""
    base = Path(base_path).resolve()
    target = Path(requested_path).resolve()
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def get_skill_files(skill_dir: Path) -> list:
    """Get list of all files in a skill directory."""
    files = []
    for file in skill_dir.glob("**/*"):
        if file.is_file():
            full_path = str(file)
            rel_path = full_path[len(str(skill_dir)) + 1 :]
            files.append(rel_path)
    return files


def sanitize(name: str) -> str:
    return name.replace(" ", "").replace("_", "").replace("-", "").lower()


def resolve_user_skills_path(ctx, user):
    if not user:
        raise ValueError("User is required")
    user_path = ctx.get_user_path(user)
    return os.path.join(user_path, "skills")

def resolve_skills_write_path(ctx, user=None):
    if user:
        user_skills_path = resolve_user_skills_path(ctx, user)
        os.makedirs(user_skills_path, exist_ok=True)
        return user_skills_path
    home_skills = ctx.get_home_path(os.path.join(".agent", "skills"))
    os.makedirs(home_skills, exist_ok=True)
    return home_skills

def resolve_all_skills(ctx, user=None):
    home_skills = ctx.get_home_path(os.path.join(".agent", "skills"))
    skill_roots = {}

    # add .claude skills first, so they can be overridden by .agent skills
    claude_skills = os.path.expanduser("~/.claude/skills")
    if os.path.exists(claude_skills):
        skill_roots["~/.claude/skills"] = claude_skills

    if os.path.exists(os.path.join(".claude", "skills")):
        skill_roots[".claude/skills"] = os.path.join(".claude", "skills")

    skill_roots[LLMS_HOME_SKILLS] = home_skills

    local_skills = os.path.join(".agent", "skills")
    if os.path.exists(local_skills):
        local_skills = str(Path(local_skills).resolve())
        skill_roots[LLMS_LOCAL_SKILLS] = local_skills

    user_skills_path = None
    if user:
        user_skills_path = resolve_user_skills_path(ctx, user)
        if os.path.exists(user_skills_path):
            skill_roots[f"{user}/skills"] = user_skills_path

    ret = {}
    for group, root in skill_roots.items():
        if not os.path.exists(root):
            continue
        try:
            for entry in os.scandir(root):
                if (
                    entry.is_dir()
                    and os.path.exists(os.path.join(entry.path, "SKILL.md"))
                    or os.path.exists(os.path.join(entry.path, "skill.md"))
                ):
                    skill_dir = Path(entry.path).resolve()
                    props = read_properties(skill_dir)

                    # recursivly list all files in this directory
                    files = []
                    for file in skill_dir.glob("**/*"):
                        if file.is_file():
                            full_path = str(file)
                            rel_path = full_path[len(str(skill_dir)) + 1 :]
                            files.append(rel_path)

                    writable = False
                    if ctx.is_auth_enabled():
                        writable = user_skills_path and is_safe_path(user_skills_path, skill_dir)
                    else:
                        writable = is_safe_path(home_skills, skill_dir) or is_safe_path(local_skills, skill_dir)

                    skill_props = props.to_dict()
                    skill_props.update(
                        {
                            "group": group,
                            "location": str(skill_dir),
                            "files": files,
                            "writable": bool(writable),
                        }
                    )
                    ret[props.name] = skill_props

        except OSError:
            pass
    return ret

def assert_valid_location(ctx, location, user):
    if ctx.is_auth_enabled() and not user:
        raise Exception("Unauthorized")

    # if user is specified, only allow modifications to skills in user directory
    if user:
        write_skill_path = resolve_skills_write_path(ctx, user=user)
        if not is_safe_path(write_skill_path, location):
            raise Exception("Cannot modify skills outside of allowed user directory")
        return

    home_skills_path = ctx.get_home_path(os.path.join(".agent", "skills"))
    local_skills_path = os.path.join(".agent", "skills")

    # Otherwise only allow modifications to skills in home or local .agent directory
    if not is_safe_path(home_skills_path, location) and not is_safe_path(local_skills_path, location):
        raise Exception("Cannot modify skills outside of allowed directories")

def install(ctx):
    home_skills = ctx.get_home_path(os.path.join(".agent", "skills"))
    # if not folder exists
    if not os.path.exists(home_skills):
        os.makedirs(ctx.get_home_path(os.path.join(".agent")), exist_ok=True)
        ctx.log(f"Creating initial skills folder: {home_skills}")
        # os.makedirs(home_skills)
        # copy ui/skills to home_skills
        ui_skills = os.path.join(ctx.path, "ui", "skills")
        shutil.copytree(ui_skills, home_skills)

    g_available_skills = []
    try:
        with open(os.path.join(ctx.path, "ui", "data", "skills-top-5000.json")) as f:
            top_skills = json.load(f)
            g_available_skills = top_skills["skills"]
    except Exception:
        pass

    async def get_skills(request):
        skills = resolve_all_skills(ctx, user=ctx.get_username(request))
        return aiohttp.web.json_response(skills)

    ctx.add_get("", get_skills)

    async def search_available_skills(request):
        q = request.query.get("q", "")
        limit = int(request.query.get("limit", 50))
        offset = int(request.query.get("offset", 0))
        q_lower = q.lower()
        filtered_results = [
            s for s in g_available_skills if q_lower in s.get("name", "") or q_lower in s.get("topSource", "")
        ]
        sorted_by_installs = sorted(filtered_results, key=lambda x: x.get("installs", 0), reverse=True)
        results = sorted_by_installs[offset : offset + limit]
        return aiohttp.web.json_response(
            {
                "results": results,
                "total": len(sorted_by_installs),
            }
        )

    ctx.add_get("search", search_available_skills)

    async def install_skill(request):
        id = request.match_info.get("id")
        skill = next((s for s in g_available_skills if s.get("id") == id), None)
        if not skill:
            raise Exception(f"Skill '{id}' not found")

        # Get the source repo (e.g., "vercel-labs/agent-skills")
        source = skill.get("topSource")
        if not source:
            raise Exception(f"Skill '{id}' has no source repository")

        user = ctx.assert_username(request)
        write_skill_path = resolve_skills_write_path(ctx, user=user)
        
        # Install from GitHub
        from .installer import install_from_github

        ctx.log(f"Installing skill '{id}' from '{source}' to '{write_skill_path}'")
        result = await install_from_github(
            repo_url=f"https://github.com/{source}.git",
            skill_names=[id],
            target_dir=write_skill_path,
        )

        if not result.get("success"):
            raise Exception(result.get("error", "Installation failed"))

        return aiohttp.web.json_response(result)

    ctx.add_post("install/{id}", install_skill)

    async def get_skill(request):
        name = request.match_info.get("name")
        file = request.query.get("file")
        user = ctx.assert_username(request)
        return aiohttp.web.Response(text=skill(name, file, user=user))

    ctx.add_get("contents/{name}", get_skill)

    async def get_file_content(request):
        """Get the content of a specific file in a skill."""
        name = request.match_info.get("name")
        file_path = request.match_info.get("path")
        user = ctx.assert_username(request)
        skills = resolve_all_skills(ctx, user=user)

        skill_info = skills.get(name)
        if not skill_info:
            raise Exception(f"Skill '{name}' not found")

        location = skill_info.get("location")
        full_path = os.path.join(location, file_path)

        if not is_safe_path(location, full_path):
            raise Exception("Invalid file path")

        if not os.path.exists(full_path):
            raise Exception(f"File '{file_path}' not found")

        try:
            with open(full_path, encoding="utf-8") as f:
                content = f.read()
            return aiohttp.web.json_response({"content": content, "path": file_path})
        except Exception as e:
            raise Exception(str(e)) from e

    ctx.add_get("file/{name}/{path:.*}", get_file_content)

    async def save_file(request):
        """Save/update a file in a skill. Only works for skills in user home or local directory."""
        name = request.match_info.get("name")

        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise Exception("Invalid JSON body") from None

        file_path = data.get("path")
        content = data.get("content")

        if not file_path or content is None:
            raise Exception("Missing 'path' or 'content' in request body")

        user = ctx.assert_username(request)
        skills = resolve_all_skills(ctx, user=user)
        skill_info = skills.get(name)
        if not skill_info:
            raise Exception(f"Skill '{name}' not found")

        location = skill_info.get("location")

        assert_valid_location(ctx, location, user)

        full_path = os.path.join(location, file_path)

        if not is_safe_path(location, full_path):
            raise Exception("Invalid file path")

        try:
            # Create parent directories if they don't exist
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Reload skill metadata
            skills = resolve_all_skills(ctx, user=user)
            skill_info = skills.get(name)

            return aiohttp.web.json_response({"path": file_path, "skill": skill_info})
        except Exception as e:
            raise Exception(str(e)) from e

    ctx.add_post("file/{name}", save_file)

    async def delete_file(request):
        """Delete a file from a skill. Only works for skills in home directory."""
        name = request.match_info.get("name")
        file_path = request.query.get("path")

        if not file_path:
            raise Exception("Missing 'path' query parameter")

        user = ctx.assert_username(request)
        skills = resolve_all_skills(ctx, user=user)
        skill_info = skills.get(name)
        if not skill_info:
            raise Exception(f"Skill '{name}' not found")

        location = skill_info.get("location")
        assert_valid_location(ctx, location, user)

        full_path = os.path.join(location, file_path)

        if not is_safe_path(location, full_path):
            raise Exception("Invalid file path")

        # Prevent deleting SKILL.md
        if file_path.lower() == "skill.md":
            raise Exception("Cannot delete SKILL.md - delete the entire skill instead")

        if not os.path.exists(full_path):
            raise Exception(f"File '{file_path}' not found")

        try:
            os.remove(full_path)

            # Clean up empty parent directories
            parent = os.path.dirname(full_path)
            while parent != location:
                if os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
                else:
                    break

            # Reload skill metadata
            skills = resolve_all_skills(ctx, user=user)
            skill_info = skills.get(name)

            return aiohttp.web.json_response({"path": file_path, "skill": skill_info})
        except Exception as e:
            raise Exception(str(e)) from e

    ctx.add_delete("file/{name}", delete_file)

    async def create_skill(request):
        """Create a new skill using the skill-creator template."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise Exception("Invalid JSON body") from None

        skill_name = data.get("name")
        if not skill_name:
            raise Exception("Missing 'name' in request body")

        # Validate skill name format
        import re

        if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", skill_name):
            raise Exception("Skill name must be lowercase, use hyphens, start/end with alphanumeric")

        if len(skill_name) > 40:
            raise Exception("Skill name must be 40 characters or less")

        user = ctx.assert_username(request)
        write_skill_path = resolve_skills_write_path(ctx, user=user)
        skill_dir = os.path.join(write_skill_path, skill_name)

        if os.path.exists(skill_dir):
            raise Exception(f"Skill '{skill_name}' already exists")

        # Use init_skill.py from skill-creator
        init_script = os.path.join(ctx.path, "ui", "skills", "skill-creator", "scripts", "init_skill.py")

        if not os.path.exists(init_script):
            raise Exception("skill-creator not found")

        try:
            import subprocess

            ctx.log(f"Creating skill '{skill_name}' in '{write_skill_path}'")
            result = subprocess.run(
                [sys.executable, init_script, skill_name, "--path", write_skill_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                raise Exception(f"Failed to create skill: {result.stderr}")

            # Load the new skill
            if os.path.exists(skill_dir):
                skills = resolve_all_skills(ctx, user=user)
                skill_info = skills.get(skill_name)
                return aiohttp.web.json_response({"skill": skill_info, "output": result.stdout})

            raise Exception("Skill directory not created")

        except subprocess.TimeoutExpired:
            raise Exception("Skill creation timed out") from None
        except Exception as e:
            raise Exception(str(e)) from e

    ctx.add_post("create", create_skill)

    async def delete_skill(request):
        """Delete an entire skill. Only works for skills in home directory."""
        name = request.match_info.get("name")

        user = ctx.assert_username(request)
        skills = resolve_all_skills(ctx, user=user)
        skill_info = skills.get(name)

        if skill_info:
            location = skill_info.get("location")
        else:
            # Check if orphaned directory exists on disk (not loaded in skills)
            potential_location = os.path.join(home_skills, name)
            if os.path.exists(potential_location):
                location = potential_location
            else:
                raise Exception(f"Skill '{name}' not found")

        # Only allow deletion of skills in allowed directories
        assert_valid_location(ctx, location, user)

        try:
            if os.path.exists(location):
                shutil.rmtree(location)

            return aiohttp.web.json_response({"deleted": name})
        except Exception as e:
            raise Exception(str(e)) from e

    ctx.add_delete("skill/{name}", delete_skill)

    def skill(name: Annotated[str, "skill name"], file: Annotated[str | None, "skill file"] = None, user=None):
        """Get the content of a skill or a specific file within a skill."""
        ctx.log(f"skill tool '{name}', file='{file}', user='{user}'")

        skills = resolve_all_skills(ctx, user=user)
        skill = skills.get(name)

        if not skill:
            sanitized_name = sanitize(name)
            for k, v in skills.items():
                if sanitize(k) == sanitized_name:
                    skill = v
                    break

        if not skill:
            return f"Error: Skill {name} not found. Available skills: {', '.join(skills.keys())}"
        location = skill.get("location")
        if not location or not os.path.exists(location):
            return f"Error: Skill {name} not found at location {location}"

        if file:
            if file.startswith(location):
                file = file[len(location) + 1 :]
            if not os.path.exists(os.path.join(location, file)):
                return f"Error: File {file} not found in skill {name}. Available files: {', '.join(skill.get('files', []))}"
            with open(os.path.join(location, file)) as f:
                return f.read()

        with open(os.path.join(location, "SKILL.md")) as f:
            content = f.read()

            files = skill.get("files")
            if files and len(files) > 1:
                content += "\n\n## Skill Files:\n```\n"
                return content

    ctx.register_tool(skill, group="core_tools")


__install__ = install
