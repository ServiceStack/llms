"""
Anthropic's Computer Use Tools
https://github.com/anthropics/claude-quickstarts/tree/main/computer-use-demo
"""

import os

from .bash import open, run_bash
from .computer import computer
from .edit import edit
from .platform import get_display_num, get_screen_resolution

width, height = get_screen_resolution()
# set enviroment variables
os.environ["WIDTH"] = str(width)
os.environ["HEIGHT"] = str(height)
os.environ["DISPLAY_NUM"] = str(get_display_num())


def install(ctx):
    ctx.register_tool(run_bash, group="computer_use")
    ctx.register_tool(open, group="computer_use")
    ctx.register_tool(edit, group="computer_use")
    ctx.register_tool(computer, group="computer_use")


__install__ = install
