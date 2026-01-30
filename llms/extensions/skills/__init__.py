import json
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated

import aiohttp

from .parser import read_properties

g_skills = {}
g_home_skills = None

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


def reload_skill(name: str, location: str, group: str):
    """Reload a single skill's metadata."""
    global g_skills
    skill_dir = Path(location).resolve()
    if not skill_dir.exists():
        if name in g_skills:
            del g_skills[name]
        return None

    props = read_properties(skill_dir)
    files = get_skill_files(skill_dir)

    skill_props = props.to_dict()
    skill_props.update(
        {
            "group": group,
            "location": str(skill_dir),
            "files": files,
        }
    )
    g_skills[props.name] = skill_props
    return skill_props


def sanitize(name: str) -> str:
    return name.replace(" ", "").replace("_", "").replace("-", "").lower()


def skill(name: Annotated[str, "skill name"], file: Annotated[str | None, "skill file"] = None):
    """Get the content of a skill or a specific file within a skill."""
    skill = g_skills.get(name)

    if not skill:
        sanitized_name = sanitize(name)
        for k, v in g_skills.items():
            if sanitize(k) == sanitized_name:
                skill = v
                break

    if not skill:
        return f"Error: Skill {name} not found. Available skills: {', '.join(g_skills.keys())}"
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
            for file in files:
                content += f"{file}\n"
            content += "```\n"
        return content


def install(ctx):
    global g_skills, g_home_skills
    home_skills = ctx.get_home_path(os.path.join(".agent", "skills"))
    g_home_skills = home_skills
    # if not folder exists
    if not os.path.exists(home_skills):
        os.makedirs(ctx.get_home_path(os.path.join(".agent")), exist_ok=True)
        ctx.log(f"Creating initial skills folder: {home_skills}")
        # os.makedirs(home_skills)
        # copy ui/skills to home_skills
        ui_skills = os.path.join(ctx.path, "ui", "skills")
        shutil.copytree(ui_skills, home_skills)

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

    g_skills = {}
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

                    skill_props = props.to_dict()
                    skill_props.update(
                        {
                            "group": group,
                            "location": str(skill_dir),
                            "files": files,
                        }
                    )
                    g_skills[props.name] = skill_props

        except OSError:
            pass

    g_available_skills = []
    try:
        with open(os.path.join(ctx.path, "ui", "data", "skills-top-5000.json")) as f:
            top_skills = json.load(f)
            g_available_skills = top_skills["skills"]
    except Exception:
        pass

    async def get_skills(request):
        return aiohttp.web.json_response(g_skills)

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

        # Install from GitHub
        from .installer import install_from_github

        result = await install_from_github(
            repo_url=f"https://github.com/{source}.git",
            skill_names=[id],
            target_dir=home_skills,
        )

        if not result.get("success"):
            raise Exception(result.get("error", "Installation failed"))

        # Reload the installed skills into the registry
        for installed in result.get("installed", []):
            skill_path = installed.get("path")
            if skill_path and os.path.exists(skill_path):
                skill_dir = Path(skill_path).resolve()
                props = read_properties(skill_dir)
                files = get_skill_files(skill_dir)
                skill_props = props.to_dict()
                skill_props.update(
                    {
                        "group": LLMS_HOME_SKILLS,
                        "location": str(skill_dir),
                        "files": files,
                    }
                )
                g_skills[props.name] = skill_props

        return aiohttp.web.json_response(result)

    ctx.add_post("install/{id}", install_skill)

    async def get_skill(request):
        name = request.match_info.get("name")
        file = request.query.get("file")
        return aiohttp.web.Response(text=skill(name, file))

    ctx.add_get("contents/{name}", get_skill)

    async def get_file_content(request):
        """Get the content of a specific file in a skill."""
        name = request.match_info.get("name")
        file_path = request.match_info.get("path")

        skill_info = g_skills.get(name)
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
        """Save/update a file in a skill. Only works for skills in home directory."""
        name = request.match_info.get("name")

        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise Exception("Invalid JSON body") from None

        file_path = data.get("path")
        content = data.get("content")

        if not file_path or content is None:
            raise Exception("Missing 'path' or 'content' in request body")

        skill_info = g_skills.get(name)
        if not skill_info:
            raise Exception(f"Skill '{name}' not found")

        location = skill_info.get("location")

        # Only allow modifications to skills in home or local .agent directory
        if not is_safe_path(home_skills, location) and not (local_skills and is_safe_path(local_skills, location)):
            raise Exception("Cannot modify skills outside of allowed directories")

        full_path = os.path.join(location, file_path)

        if not is_safe_path(location, full_path):
            raise Exception("Invalid file path")

        try:
            # Create parent directories if they don't exist
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Reload skill metadata
            group = skill_info.get("group", LLMS_HOME_SKILLS)
            updated_skill = reload_skill(name, location, group)

            return aiohttp.web.json_response({"path": file_path, "skill": updated_skill})
        except Exception as e:
            raise Exception(str(e)) from e

    ctx.add_post("file/{name}", save_file)

    async def delete_file(request):
        """Delete a file from a skill. Only works for skills in home directory."""
        name = request.match_info.get("name")
        file_path = request.query.get("path")

        if not file_path:
            raise Exception("Missing 'path' query parameter")

        skill_info = g_skills.get(name)
        if not skill_info:
            raise Exception(f"Skill '{name}' not found")

        location = skill_info.get("location")

        # Only allow modifications to skills in home or local .agent directory
        if not is_safe_path(home_skills, location) and not (local_skills and is_safe_path(local_skills, location)):
            raise Exception("Cannot modify skills outside of allowed directories")

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
            group = skill_info.get("group", LLMS_HOME_SKILLS)
            updated_skill = reload_skill(name, location, group)

            return aiohttp.web.json_response({"path": file_path, "skill": updated_skill})
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

        skill_dir = os.path.join(home_skills, skill_name)

        if os.path.exists(skill_dir):
            raise Exception(f"Skill '{skill_name}' already exists")

        # Use init_skill.py from skill-creator
        init_script = os.path.join(ctx.path, "ui", "skills", "skill-creator", "scripts", "init_skill.py")

        if not os.path.exists(init_script):
            raise Exception("skill-creator not found")

        try:
            import subprocess

            result = subprocess.run(
                [sys.executable, init_script, skill_name, "--path", home_skills],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                raise Exception(f"Failed to create skill: {result.stderr}")

            # Load the new skill
            if os.path.exists(skill_dir):
                skill_dir_path = Path(skill_dir).resolve()
                props = read_properties(skill_dir_path)
                files = get_skill_files(skill_dir_path)

                skill_props = props.to_dict()
                skill_props.update(
                    {
                        "group": LLMS_HOME_SKILLS,
                        "location": str(skill_dir_path),
                        "files": files,
                    }
                )
                g_skills[props.name] = skill_props

                return aiohttp.web.json_response({"skill": skill_props, "output": result.stdout})

            raise Exception("Skill directory not created")

        except subprocess.TimeoutExpired:
            raise Exception("Skill creation timed out") from None
        except Exception as e:
            raise Exception(str(e)) from e

    ctx.add_post("create", create_skill)

    async def delete_skill(request):
        """Delete an entire skill. Only works for skills in home directory."""
        name = request.match_info.get("name")

        skill_info = g_skills.get(name)

        if skill_info:
            location = skill_info.get("location")
        else:
            # Check if orphaned directory exists on disk (not loaded in g_skills)
            potential_location = os.path.join(home_skills, name)
            if os.path.exists(potential_location) and is_safe_path(home_skills, potential_location):
                location = potential_location
            else:
                raise Exception(f"Skill '{name}' not found")

        # Only allow deletion of skills in home or local .agent directory
        if not is_safe_path(home_skills, location) and not (local_skills and is_safe_path(local_skills, location)):
            raise Exception("Cannot delete skills outside of allowed directories")

        try:
            if os.path.exists(location):
                shutil.rmtree(location)
            if name in g_skills:
                del g_skills[name]

            return aiohttp.web.json_response({"deleted": name})
        except Exception as e:
            raise Exception(str(e)) from e

    ctx.add_delete("skill/{name}", delete_skill)

    ctx.register_tool(skill, group="core_tools")


__install__ = install
