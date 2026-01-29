"""
Skill installer module for cloning and installing skills from GitHub repositories.
"""

import asyncio
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .parser import read_properties


@dataclass
class Skill:
    """Skill metadata from SKILL.md frontmatter."""

    name: str
    description: str
    path: Path
    raw_content: str | None = None
    metadata: dict | None = None


@dataclass
class InstallResult:
    """Result of a skill installation."""

    success: bool
    path: str
    skill_name: str
    error: str | None = None


class GitCloneError(Exception):
    """Error during git clone operation."""

    def __init__(self, message: str, url: str, is_timeout: bool = False, is_auth_error: bool = False):
        super().__init__(message)
        self.url = url
        self.is_timeout = is_timeout
        self.is_auth_error = is_auth_error


CLONE_TIMEOUT_SECONDS = 60
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__"}
EXCLUDE_FILES = {"README.md", "metadata.json"}
EXCLUDE_DIRS = {".git"}


def sanitize_name(name: str) -> str:
    """
    Sanitize a skill name for safe filesystem usage (kebab-case).

    - Converts to lowercase
    - Replaces non-alphanumeric chars (except dots/underscores) with hyphens
    - Removes leading/trailing dots and hyphens
    - Limits to 255 chars
    """
    sanitized = name.lower()
    # Replace any sequence of chars that are NOT lowercase letters, digits, dots, or underscores
    sanitized = re.sub(r"[^a-z0-9._]+", "-", sanitized)
    # Remove leading/trailing dots and hyphens
    sanitized = re.sub(r"^[.\-]+|[.\-]+$", "", sanitized)
    # Limit to 255 chars, fallback to 'unnamed-skill' if empty
    return sanitized[:255] or "unnamed-skill"


def is_path_safe(base_path: str, target_path: str) -> bool:
    """Validate that a path is within an expected base directory."""
    base = Path(base_path).resolve()
    target = Path(target_path).resolve()
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


async def clone_repo(url: str, ref: str | None = None) -> str:
    """
    Clone a git repository to a temp directory.

    Args:
        url: Git repository URL
        ref: Optional branch/tag/commit reference

    Returns:
        Path to the cloned repository temp directory

    Raises:
        GitCloneError: If clone fails
    """
    temp_dir = tempfile.mkdtemp(prefix="skills-")

    clone_args = ["git", "clone", "--depth", "1"]
    if ref:
        clone_args.extend(["--branch", ref])
    clone_args.extend([url, temp_dir])

    try:
        process = await asyncio.create_subprocess_exec(
            *clone_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=CLONE_TIMEOUT_SECONDS)

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            await cleanup_temp_dir(temp_dir)

            is_auth_error = any(
                msg in error_msg
                for msg in [
                    "Authentication failed",
                    "could not read Username",
                    "Permission denied",
                    "Repository not found",
                ]
            )

            if is_auth_error:
                raise GitCloneError(
                    f"Authentication failed for {url}.\n"
                    "  - For private repos, ensure you have access\n"
                    "  - For SSH: Check your keys with 'ssh -T git@github.com'\n"
                    "  - For HTTPS: Run 'gh auth login' or configure git credentials",
                    url,
                    is_auth_error=True,
                )

            raise GitCloneError(f"Failed to clone {url}: {error_msg}", url)

        return temp_dir

    except asyncio.TimeoutError:
        await cleanup_temp_dir(temp_dir)
        raise GitCloneError(
            f"Clone timed out after {CLONE_TIMEOUT_SECONDS}s. This often happens with private repos.\n"
            "  Ensure you have access and your SSH keys or credentials are configured.",
            url,
            is_timeout=True,
        ) from None


async def cleanup_temp_dir(dir_path: str) -> None:
    """Clean up a temp directory safely (only if within system tempdir)."""
    normalized_dir = Path(dir_path).resolve()
    normalized_tmp = Path(tempfile.gettempdir()).resolve()

    if not str(normalized_dir).startswith(str(normalized_tmp)):
        raise ValueError("Attempted to clean up directory outside of temp directory")

    shutil.rmtree(dir_path, ignore_errors=True)


def parse_skill_md(skill_md_path: Path) -> Skill | None:
    """Parse a SKILL.md file and return skill metadata."""
    try:
        props = read_properties(skill_md_path.parent)
        if not props.name or not props.description:
            return None

        content = skill_md_path.read_text(encoding="utf-8")
        return Skill(
            name=props.name,
            description=props.description,
            path=skill_md_path.parent,
            raw_content=content,
            metadata=props.metadata,
        )
    except Exception:
        return None


def has_skill_md(dir_path: Path) -> bool:
    """Check if a directory contains a SKILL.md file."""
    return (dir_path / "SKILL.md").is_file() or (dir_path / "skill.md").is_file()


def find_skill_dirs(dir_path: Path, depth: int = 0, max_depth: int = 5) -> list[Path]:
    """Recursively find directories containing SKILL.md files."""
    if depth > max_depth:
        return []

    result = []
    try:
        if has_skill_md(dir_path):
            result.append(dir_path)

        for entry in dir_path.iterdir():
            if entry.is_dir() and entry.name not in SKIP_DIRS:
                result.extend(find_skill_dirs(entry, depth + 1, max_depth))
    except OSError:
        pass

    return result


async def discover_skills(base_path: str, subpath: str | None = None) -> list[Skill]:
    """
    Discover skills in a directory by finding SKILL.md files.

    Searches priority directories first (common skill locations), then falls back
    to recursive search if nothing found.
    """
    skills: list[Skill] = []
    seen_names: set[str] = set()
    search_path = Path(base_path) / subpath if subpath else Path(base_path)

    # If pointing directly at a skill, return just that
    if has_skill_md(search_path):
        skill = parse_skill_md(search_path / "SKILL.md")
        if skill:
            return [skill]

    # Search common skill locations first
    priority_dirs = [
        search_path,
        search_path / "skills",
        search_path / "skills" / ".curated",
        search_path / "skills" / ".experimental",
        search_path / "skills" / ".system",
        search_path / ".agent" / "skills",
        search_path / ".agents" / "skills",
        search_path / ".claude" / "skills",
    ]

    for dir_path in priority_dirs:
        if not dir_path.is_dir():
            continue

        try:
            for entry in dir_path.iterdir():
                if entry.is_dir() and has_skill_md(entry):
                    skill = parse_skill_md(entry / "SKILL.md")
                    if skill and skill.name not in seen_names:
                        skills.append(skill)
                        seen_names.add(skill.name)
        except OSError:
            pass

    # Fall back to recursive search if nothing found
    if not skills:
        all_skill_dirs = find_skill_dirs(search_path)
        for skill_dir in all_skill_dirs:
            skill = parse_skill_md(skill_dir / "SKILL.md")
            if skill and skill.name not in seen_names:
                skills.append(skill)
                seen_names.add(skill.name)

    return skills


def is_excluded(name: str, is_directory: bool = False) -> bool:
    """Check if a file/directory should be excluded from copying."""
    if name in EXCLUDE_FILES:
        return True
    if name.startswith("_"):
        return True
    return is_directory and name in EXCLUDE_DIRS


def copy_skill_directory(src: Path, dest: Path) -> None:
    """Copy a skill directory, excluding certain files."""
    dest.mkdir(parents=True, exist_ok=True)

    for entry in src.iterdir():
        if is_excluded(entry.name, entry.is_dir()):
            continue

        dest_path = dest / entry.name
        if entry.is_dir():
            copy_skill_directory(entry, dest_path)
        else:
            shutil.copy2(entry, dest_path)


async def install_skill(skill: Skill, target_base: str) -> InstallResult:
    """
    Install a skill to the target directory.

    Args:
        skill: Skill to install
        target_base: Base directory for skill installation (e.g., ~/.llms/.agents/skills)

    Returns:
        InstallResult with success status and path
    """
    skill_name = sanitize_name(skill.name)
    target_dir = Path(target_base) / skill_name

    # Validate path safety
    if not is_path_safe(target_base, str(target_dir)):
        return InstallResult(
            success=False,
            path=str(target_dir),
            skill_name=skill_name,
            error="Invalid skill name: potential path traversal detected",
        )

    try:
        # Remove existing skill directory if it exists
        if target_dir.exists():
            shutil.rmtree(target_dir)

        # Copy skill files
        copy_skill_directory(skill.path, target_dir)

        return InstallResult(
            success=True,
            path=str(target_dir),
            skill_name=skill_name,
        )
    except Exception as e:
        return InstallResult(
            success=False,
            path=str(target_dir),
            skill_name=skill_name,
            error=str(e),
        )


def filter_skills(skills: list[Skill], skill_names: list[str]) -> list[Skill]:
    """Filter skills by name (case-insensitive)."""
    normalized_names = [n.lower() for n in skill_names]
    return [s for s in skills if s.name.lower() in normalized_names or sanitize_name(s.name) in normalized_names]


async def install_from_github(
    repo_url: str,
    ref: str | None = None,
    subpath: str | None = None,
    skill_names: list[str] | None = None,
    target_dir: str | None = None,
) -> dict:
    """
    Install skill(s) from a GitHub repository.

    Args:
        repo_url: GitHub repository URL (e.g., https://github.com/owner/repo.git)
        ref: Optional branch/tag/commit reference
        subpath: Optional subdirectory within the repo to search for skills
        skill_names: Optional list of skill names to install (installs all if None)
        target_dir: Target directory for installation (defaults to ~/.llms/.agents/skills)

    Returns:
        Dictionary with installation results
    """
    if target_dir is None:
        target_dir = os.path.expanduser("~/.llms/.agents/skills")

    # Ensure target directory exists
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    temp_dir = None
    try:
        # Clone the repository
        temp_dir = await clone_repo(repo_url, ref)

        # Discover skills in the repo
        skills = await discover_skills(temp_dir, subpath)

        if not skills:
            return {
                "success": False,
                "error": "No skills found in repository",
                "installed": [],
            }

        # Filter skills if specific names requested
        if skill_names:
            skills = filter_skills(skills, skill_names)
            if not skills:
                return {
                    "success": False,
                    "error": f"No matching skills found for: {', '.join(skill_names)}",
                    "installed": [],
                }

        # Install each skill
        results = []
        for skill in skills:
            result = await install_skill(skill, target_dir)
            results.append(
                {
                    "name": result.skill_name,
                    "path": result.path,
                    "success": result.success,
                    "error": result.error,
                }
            )

        successful = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]

        return {
            "success": len(failed) == 0,
            "installed": successful,
            "failed": failed,
            "total": len(results),
        }

    except GitCloneError as e:
        return {
            "success": False,
            "error": str(e),
            "installed": [],
        }
    finally:
        if temp_dir:
            await cleanup_temp_dir(temp_dir)
