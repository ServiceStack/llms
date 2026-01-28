"""
Anthropic's Filesystem MCP Tools
https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem

filesystem
{
  "command": "npx",
  "args": [
    "-y",
    "@modelcontextprotocol/server-filesystem",
    "$PWD",
    "$LLMS_HOME/.agent"
  ]
}
"""

import base64
import difflib
import fnmatch
import logging
import mimetypes
import os
import shutil
import time
from typing import Annotated, Any, Dict, List, Literal, Optional

# Configure logging
logger = logging.getLogger(__name__)

g_ctx = None


def filesystem_init(ctx):
    global g_ctx
    g_ctx = ctx


def get_app():
    if g_ctx is None:
        raise RuntimeError("Filesystem extension not initialized")
    return g_ctx


def set_allowed_directories(directories: List[str]) -> None:
    """Set the list of allowed directories.

    Args:
        directories: List of absolute paths that are allowed to be accessed.
    """
    get_app().set_allowed_directories(directories)


def add_allowed_directory(path: str) -> None:
    """Add a directory to the allowed list.

    Args:
        path: Absolute path to add.
    """
    get_app().add_allowed_directory(path)


def get_allowed_directories() -> List[str]:
    """
    Returns the list of directories that this server is allowed to access.
    """
    return get_app().get_allowed_directories()


def list_allowed_directories() -> str:
    """
    Returns the list of directories that this server is allowed to access. Subdirectories within these allowed directories are also accessible.
    Use this to understand which directories and their nested paths are available before trying to access files.
    """
    return "Allowed directories:\n" + "\n".join(get_app().get_allowed_directories())


def _validate_path(path_str: str) -> str:
    """Validate that the path is within one of the allowed directories.

    Args:
        path_str: The path to validate.

    Returns:
        The absolute validated path.

    Raises:
        ValueError: If path is invalid or not allowed.
    """
    if not path_str:
        raise ValueError("Path cannot be empty")

    # Expand user (~)
    path_str = os.path.expanduser(path_str)

    # Get absolute path
    try:
        abs_path = os.path.abspath(path_str)
    except Exception as e:
        raise ValueError(f"Invalid path: {e}") from e

    # Check if path is within any allowed directory
    is_allowed = False
    for allowed_dir in get_allowed_directories():
        # Check if abs_path starts with allowed_dir
        # We add os.sep to ensure we don't match /app2 when allowed is /app
        allowed_dir_str = str(allowed_dir)
        if not allowed_dir_str.endswith(os.sep):
            allowed_dir_str += os.sep

        if abs_path.startswith(allowed_dir_str) or abs_path == allowed_dir:
            is_allowed = True
            break

    if not is_allowed:
        raise ValueError(
            f"Access denied: {abs_path} is not within allowed directories:\n{', '.join(get_allowed_directories())}"
        )

    return abs_path


def _format_size(size_bytes: int) -> str:
    """Format size in bytes to human readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def _is_binary(path_str: str) -> bool:
    """Check if file is binary (rudimentary check)."""
    try:
        with open(path_str) as check_file:
            check_file.read(1024)
            return False
    except Exception:
        return True


def read_text_file(
    path: Annotated[str, "Path to the file."],
    head: Annotated[Optional[int], "If provided, returns only the first N lines of the file"] = None,
    tail: Annotated[Optional[int], "If provided, returns only the last N lines of the file"] = None,
) -> str:
    """
    Read the complete contents of a file from the file system as text. Handles various text encodings and provides detailed error messages if the file cannot be read.
    Use this tool when you need to examine the contents of a single file. Use the 'head' parameter to read only the first N lines of a file, or the 'tail' parameter to read only the last N lines of a file.
    Operates on the file as text regardless of extension. Only works within allowed directories.
    Returns: The content of the file.
    """
    valid_path = _validate_path(path)

    if head is not None and tail is not None:
        raise ValueError("Cannot specify both head and tail parameters simultaneously")

    if not os.path.exists(valid_path):
        raise FileNotFoundError(f"File not found: {valid_path}")

    if not os.path.isfile(valid_path):
        raise ValueError(f"Path is not a file: {valid_path}")

    try:
        with open(valid_path, encoding="utf-8", errors="replace") as f:
            if head is not None:
                lines = []
                for _ in range(head):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line)
                return "".join(lines)

            if tail is not None:
                # This could be optimized for large files but reading all lines is safer for simple impl
                lines = f.readlines()
                return "".join(lines[-tail:])

            return f.read()
    except Exception as e:
        raise RuntimeError(f"Error reading file {valid_path}: {e}") from e


def read_media_file(path: Annotated[str, "Path to the file"]) -> Dict[str, Any]:
    """
    Read an image or audio file.
    Returns the base64 encoded data and MIME type. Only works within allowed directories.
    """
    valid_path = _validate_path(path)

    if not os.path.exists(valid_path):
        raise FileNotFoundError(f"File not found: {valid_path}")

    mime_type, _ = mimetypes.guess_type(valid_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    try:
        with open(valid_path, "rb") as f:
            data = f.read()
            b64_data = base64.b64encode(data).decode("utf-8")

        file_type = "blob"
        if mime_type.startswith("image/"):
            file_type = "image"
        elif mime_type.startswith("audio/"):
            file_type = "audio"

        return {"type": file_type, "data": b64_data, "mimeType": mime_type}
    except Exception as e:
        raise RuntimeError(f"Error reading media file {valid_path}: {e}") from e


def read_multiple_files(paths: Annotated[List[str], "List of file paths to read"]) -> str:
    """
    Read the contents of multiple files simultaneously. This is more efficient than reading files one by one when you need to analyze or compare multiple files.
    Each file's content is returned with its path as a reference. Failed reads for individual files won't stop the entire operation. Only works within allowed directories.
    Returns: Concatenated contents with file separators.
    """
    results = []
    for p in paths:
        try:
            content = read_text_file(p)
            results.append(f"{p}:\n{content}\n")
        except Exception as e:
            results.append(f"{p}: Error - {e}")

    return "\n---\n".join(results)


def write_file(path: Annotated[str, "Path to the file"], content: Annotated[str, "Content to write"]) -> str:
    """
    Create a new file or completely overwrite an existing file with new content. Use with caution as it will overwrite existing files without warning. Handles text content with proper encoding. Only works within allowed directories.
    Returns: Success message.
    """
    valid_path = _validate_path(path)

    try:
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(valid_path), exist_ok=True)

        with open(valid_path, "w", encoding="utf-8") as f:
            f.write(content)

        return f"Successfully wrote to {path}"
    except Exception as e:
        raise RuntimeError(f"Error writing to file {valid_path}: {e}") from e


def edit_file(
    path: Annotated[str, "Path to the file"],
    edits: Annotated[List[Dict[str, str]], "List of dicts with 'oldText' and 'newText'"],
    dry_run: bool = False,
) -> str:
    """
    Make line-based edits to a text file. Each edit replaces exact line sequences with new content.
    Returns a git-style diff showing the changes made. Only works within allowed directories.
    """

    # Example edits: [{"oldText":"boy","newText":"girl"}]

    valid_path = _validate_path(path)

    if not os.path.exists(valid_path):
        raise FileNotFoundError(f"File not found: {valid_path}")

    with open(valid_path, encoding="utf-8") as f:
        original_content = f.read()

    current_content = original_content

    # Apply edits sequentially
    for edit in edits:
        old_text = edit.get("oldText", "")
        new_text = edit.get("newText", "")

        if old_text not in current_content:
            raise ValueError(f"Could not find exact match for text to replace: {old_text[:50]}...")

        # Replace only the first occurrence to be safe?
        # The Node impl likely replaces one instance or all?
        # Usually exact match replacement implies replacing the instance found.
        # Python's replace replaces all by default, so we limit to 1.
        current_content = current_content.replace(old_text, new_text, 1)

    # Generate diff
    original_lines = original_content.splitlines(keepends=True)
    new_lines = current_content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(original_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))

    diff_text = "".join(diff)

    if not dry_run:
        with open(valid_path, "w", encoding="utf-8") as f:
            f.write(current_content)

    return diff_text


def create_directory(path: Annotated[str, "Path to the directory"]) -> str:
    """
    Create a new directory or ensure a directory exists. Can create multiple nested directories in one operation. If the directory already exists, this operation will succeed silently.
    Perfect for setting up directory structures for projects or ensuring required paths exist. Only works within allowed directories.
    Returns: Success message.
    """
    valid_path = _validate_path(path)

    try:
        os.makedirs(valid_path, exist_ok=True)
        return f"Successfully created directory {path}"
    except Exception as e:
        raise RuntimeError(f"Error creating directory {valid_path}: {e}") from e


def list_directory(path: Annotated[str, "Path to the directory"]) -> str:
    """
    Get a detailed listing of all files and directories in a specified path. Results clearly distinguish between files and directories with [FILE] and [DIR] prefixes.
    This tool is essential for understanding directory structure and finding specific files within a directory. Only works within allowed directories.
    """
    valid_path = _validate_path(path)

    if not os.path.exists(valid_path):
        raise FileNotFoundError(f"Directory not found: {valid_path}")

    if not os.path.isdir(valid_path):
        raise ValueError(f"Path is not a directory: {valid_path}")

    try:
        entries = sorted(os.listdir(valid_path))
        result = []
        for entry in entries:
            full_path = os.path.join(valid_path, entry)
            if os.path.isdir(full_path):
                result.append(f"[DIR] {entry}")
            else:
                result.append(f"[FILE] {entry}")
        return "\n".join(result)
    except Exception as e:
        raise RuntimeError(f"Error listing directory {valid_path}: {e}") from e


def list_directory_with_sizes(
    path: Annotated[str, "Path to the directory"],
    sort_by: Annotated[Literal["name", "size"], "Sort by name or size"] = "name",
) -> str:
    """
    Get a detailed listing of all files and directories in a specified path, including sizes. Results clearly distinguish between files and directories with [FILE] and [DIR] prefixes.
    This tool is useful for understanding directory structure and finding specific files within a directory. Only works within allowed directories.
    """
    valid_path = _validate_path(path)

    if not os.path.exists(valid_path):
        raise FileNotFoundError(f"Directory not found: {valid_path}")

    try:
        entries = []
        with os.scandir(valid_path) as it:
            for entry in it:
                try:
                    stats = entry.stat()
                    entries.append(
                        {"name": entry.name, "is_dir": entry.is_dir(), "size": stats.st_size, "mtime": stats.st_mtime}
                    )
                except OSError:
                    # Skip entries we can't stat
                    continue

        # Sort
        if sort_by == "size":
            entries.sort(key=lambda x: x["size"], reverse=True)
        else:
            entries.sort(key=lambda x: x["name"])

        # Format
        lines = []
        total_files = 0
        total_dirs = 0
        total_size = 0

        for e in entries:
            prefix = "[DIR] " if e["is_dir"] else "[FILE]"
            name_padded = e["name"].ljust(30)
            size_str = "" if e["is_dir"] else _format_size(e["size"]).rjust(10)
            lines.append(f"{prefix} {name_padded} {size_str}")

            if e["is_dir"]:
                total_dirs += 1
            else:
                total_files += 1
                total_size += e["size"]

        # Summary
        lines.append("")
        lines.append(f"Total: {total_files} files, {total_dirs} directories")
        lines.append(f"Combined size: {_format_size(total_size)}")

        return "\n".join(lines)
    except Exception as e:
        raise RuntimeError(f"Error listing directory {valid_path}: {e}") from e


def directory_tree(
    path: Annotated[str, "Path to the root directory"],
    exclude_patterns: Annotated[List[str], "Glob patterns to exclude"] = None,
) -> str:
    """
    Get a recursive tree view of files and directories as a JSON structure. Each entry includes 'name', 'type' (file/directory), and 'children' for directories.
    Files have no children array, while directories always have a children array (which may be empty). Respects any .gitignore rules from the root directory together with any exclude_patterns.
    The output is formatted with 2-space indentation for readability. Only works within allowed directories.
    """
    import json

    valid_path = _validate_path(path)
    root_path_len = len(valid_path.rstrip(os.sep)) + 1
    if exclude_patterns is None:
        exclude_patterns = []

    def _parse_gitignore(directory: str) -> List[str]:
        gitignore_path = os.path.join(directory, ".gitignore")
        patterns = []
        if os.path.exists(gitignore_path) and os.path.isfile(gitignore_path):
            try:
                with open(gitignore_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            patterns.append(line)
            except Exception as e:
                logger.warning(f"Error reading .gitignore in {directory}: {e}")
        return patterns

    # Parse .gitignore only in the root directory (Simple Global Mappings)
    gitignore_patterns = _parse_gitignore(valid_path)
    all_exclude_patterns = exclude_patterns + gitignore_patterns

    def _build_tree(current_path: str) -> List[Dict[str, Any]]:
        entries = []
        try:
            with os.scandir(current_path) as it:
                items = sorted(it, key=lambda x: x.name)

            for entry in items:
                # 1. Check exclusion patterns
                rel_path_from_root = entry.path[root_path_len:]

                should_exclude = False
                for pattern in all_exclude_patterns:
                    # Match against relative path or name
                    # Support ending with / for directory matching
                    is_dir_pattern = pattern.endswith("/")
                    norm_pattern = pattern.rstrip("/")

                    if fnmatch.fnmatch(rel_path_from_root, pattern) or fnmatch.fnmatch(entry.name, pattern):
                        should_exclude = True
                        break

                    # Handle patterns like "node_modules/" matching "node_modules" directory
                    if fnmatch.fnmatch(entry.name, norm_pattern):
                        if is_dir_pattern and not entry.is_dir():
                            continue
                        should_exclude = True
                        break

                if should_exclude:
                    continue

                entry_data = {"name": entry.name, "type": "directory" if entry.is_dir() else "file"}

                if entry.is_dir():
                    entry_data["children"] = _build_tree(entry.path)

                entries.append(entry_data)
        except OSError as e:
            logger.warning(f"Error scanning {current_path}: {e}")

        return entries

    tree_data = _build_tree(valid_path)
    return json.dumps(tree_data, indent=2)


def move_file(source: Annotated[str, "Source path"], destination: Annotated[str, "Destination path"]) -> str:
    """
    Move or rename files and directories. Can move files between directories and rename them in a single operation. If the destination exists, the operation will fail.
    Works across different directories and can be used for simple renaming within the same directory. Both source and destination must be within allowed directories.
    """
    valid_source = _validate_path(source)
    valid_dest = _validate_path(destination)

    try:
        shutil.move(valid_source, valid_dest)
        return f"Successfully moved {source} to {destination}"
    except Exception as e:
        raise RuntimeError(f"Error moving {source} to {destination}: {e}") from e


def search_files(
    path: Annotated[str, "Path to search in"],
    pattern: Annotated[str, "Glob pattern to match"],
    exclude_patterns: Annotated[List[str], "Glob patterns to exclude"] = None,
) -> str:
    """
    Recursively search for files and directories matching a pattern. The patterns should be glob-style patterns that match paths relative to the working directory.
    Use pattern like '.ext' to match files in current directory, and '**/.ext' to match files in all subdirectories.
    Returns full paths to all matching items. Great for finding files when you don't know their exact location. Only searches within allowed directories.
    """
    valid_path = _validate_path(path)
    results = []
    if exclude_patterns is None:
        exclude_patterns = []

    try:
        for root, dirs, files in os.walk(valid_path):
            # Check exclusions for directories to prune traversal
            dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, pat) for pat in exclude_patterns)]

            # Check all files and directories
            all_entries = dirs + files

            for entry in all_entries:
                full_path = os.path.join(root, entry)
                rel_path = os.path.relpath(full_path, valid_path)

                # Check if matches search pattern
                if fnmatch.fnmatch(entry, pattern) or fnmatch.fnmatch(rel_path, pattern):  # noqa: SIM102
                    # Double check exclusions (redundant for dirs but safe)
                    if not any(
                        fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(entry, pat) for pat in exclude_patterns
                    ):
                        results.append(full_path)

    except Exception as e:
        raise RuntimeError(f"Error searching files in {valid_path}: {e}") from e

    if not results:
        return "No matches found"

    return "\n".join(results)


def get_file_info(path: Annotated[str, "Path to the file"]) -> str:
    """
    Retrieve detailed metadata about a file or directory.
    Returns comprehensive information including size, creation time, last modified time, permissions, and type. This tool is perfect for understanding file characteristics without reading the actual content. Only works within allowed directories.
    """
    valid_path = _validate_path(path)

    try:
        stats = os.stat(valid_path)
        is_dir = os.path.isdir(valid_path)
        is_file = os.path.isfile(valid_path)

        def format_date(timestamp: float) -> str:
            return time.strftime("%a %b %d %Y %H:%M:%S GMT%z (%Z)", time.localtime(timestamp))

        # Try to get birthtime, fallback to ctime
        created_time = getattr(stats, "st_birthtime", stats.st_ctime)

        info = [
            f"size: {stats.st_size}",
            f"created: {format_date(created_time)}",
            f"modified: {format_date(stats.st_mtime)}",
            f"accessed: {format_date(stats.st_atime)}",
            f"isDirectory: {'true' if is_dir else 'false'}",
            f"isFile: {'true' if is_file else 'false'}",
            f"permissions: {oct(stats.st_mode)[-3:]}",
        ]

        return "\n".join(info)

    except Exception as e:
        raise RuntimeError(f"Error getting file info for {valid_path}: {e}") from e
