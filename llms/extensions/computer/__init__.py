"""
Anthropic's Computer Use Tools
https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-demo
"""

import os

from .bash import open, run_bash
from .edit import edit
from .filesystem import (
    create_directory,
    directory_tree,
    edit_file,
    filesystem_init,
    get_file_info,
    list_allowed_directories,
    list_directory,
    list_directory_with_sizes,
    move_file,
    read_media_file,
    read_multiple_files,
    read_text_file,
    search_files,
    write_file,
)

# Try to detect screen resolution - may fail in headless environments (Docker, etc.)
_has_display = False
try:
    from .platform import get_display_num, get_screen_resolution
    width, height = get_screen_resolution()
    # set environment variables
    os.environ["WIDTH"] = str(width)
    os.environ["HEIGHT"] = str(height)
    os.environ["DISPLAY_NUM"] = str(get_display_num())
    _has_display = True
    from .computer import computer
except RuntimeError:
    # No display server available - computer tool will not be registered
    computer = None


def install(ctx):
    filesystem_init(ctx)

    ctx.register_tool(run_bash, group="computer")
    ctx.register_tool(open, group="computer")
    ctx.register_tool(edit, group="computer")
    if _has_display and computer is not None:
        ctx.register_tool(computer, group="computer")

    ctx.register_tool(read_text_file, group="filesystem")
    ctx.register_tool(read_media_file, group="filesystem")
    ctx.register_tool(read_multiple_files, group="filesystem")
    ctx.register_tool(write_file, group="filesystem")
    ctx.register_tool(edit_file, group="filesystem")
    ctx.register_tool(create_directory, group="filesystem")
    ctx.register_tool(list_directory, group="filesystem")
    ctx.register_tool(list_directory_with_sizes, group="filesystem")
    ctx.register_tool(directory_tree, group="filesystem")
    ctx.register_tool(move_file, group="filesystem")
    ctx.register_tool(search_files, group="filesystem")
    ctx.register_tool(get_file_info, group="filesystem")
    ctx.register_tool(list_allowed_directories, group="filesystem")


__install__ = install
