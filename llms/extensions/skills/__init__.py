import os
import shutil
from pathlib import Path
from typing import Annotated

import aiohttp

from .parser import read_properties

g_skills = {}


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
    global g_skills
    home_skills = ctx.get_home_path(os.path.join(".agent", "skills"))
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
    if os.path.exists("~/.claude/skills"):
        skill_roots["~/.claude/skills"] = "~/.claude/skills"

    if os.path.exists(os.path.join(".claude", "skills")):
        skill_roots[".claude/skills"] = os.path.join(".claude", "skills")

    skill_roots["~/.llms/.agents"] = home_skills

    if os.path.exists(os.path.join(".agent", "skills")):
        skill_roots[".agents"] = os.path.join(".agent", "skills")

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

    async def get_skills(request):
        return aiohttp.web.json_response(g_skills)

    ctx.add_get("", get_skills)

    async def get_skill(request):
        name = request.match_info.get("name")
        file = request.query.get("file")
        return aiohttp.web.Response(text=skill(name, file))

    ctx.add_get("contents/{name}", get_skill)

    ctx.register_tool(skill, group="core_tools")


__install__ = install
