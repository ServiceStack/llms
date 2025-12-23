"""
Example of registering tools via an extension.
Save this file as `__init__.py` in a directory inside your extensions folder (e.g., `~/.llms/extensions/my_tools/`).
"""

import ast
import json
import math
import operator
import os
from datetime import datetime


def get_current_time(timezone: str = "UTC") -> str:
    """Get current time in the specified timezone"""
    return f"The time is {datetime.now().strftime('%I:%M %p')} {timezone}"


def read_file(file_path: str) -> str:
    """Read content of a file"""
    if not os.path.exists(file_path):
        return f"Error: File not found: {file_path}"
    with open(file_path) as f:
        return f.read()


def list_directory(path: str) -> str:
    """List directory contents"""
    if not os.path.exists(path):
        return f"Error: Path not found: {path}"

    items = []
    try:
        for entry in os.scandir(path):
            stat = entry.stat()
            items.append(
                {
                    "name": "/" + entry.name if entry.is_dir() else entry.name,
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
        return json.dumps({"path": path, "items": items}, indent=2)
    except Exception as e:
        return f"Error listing directory: {e}"


def calc(expression: str) -> str:
    """Evaluate a mathematical expression"""

    # 1. Define allowed operators
    operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.Mod: operator.mod,
    }

    # 2. Define allowed math functions and constants
    allowed_functions = {name: getattr(math, name) for name in dir(math) if not name.startswith("_")}
    allowed_functions["mod"] = operator.mod

    def eval_node(node):
        if isinstance(node, ast.Constant):  # Numbers
            return node.value

        elif isinstance(node, ast.BinOp):  # Binary Ops (1 + 2)
            return operators[type(node.op)](eval_node(node.left), eval_node(node.right))

        elif isinstance(node, ast.UnaryOp):  # Unary Ops (-5)
            return operators[type(node.op)](eval_node(node.operand))

        elif isinstance(node, ast.Call):  # Function calls (sqrt(16))
            func_name = node.func.id
            if func_name in allowed_functions:
                args = [eval_node(arg) for arg in node.args]
                return allowed_functions[func_name](*args)
            raise NameError(f"Function '{func_name}' is not allowed.")

        elif isinstance(node, ast.Name):  # Constants (pi, e)
            if node.id in allowed_functions:
                return allowed_functions[node.id]
            raise NameError(f"Variable '{node.id}' is not defined.")

        else:
            raise TypeError(f"Unsupported operation: {type(node).__name__}")

    # Parse and evaluate
    node = ast.parse(expression, mode="eval").body
    return eval_node(node)


def install(ctx):
    # Examples of registering tools using automatic definition generation
    ctx.register_tool(get_current_time)
    ctx.register_tool(read_file)
    ctx.register_tool(list_directory)

    # Register calculate tool with manual definition
    ctx.register_tool(
        calc,
        {
            "type": "function",
            "function": {
                "name": "calc",
                "description": "Evaluate a mathematical expression",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "The mathematical expression to evaluate (e.g. '3 * 4 + 5')",
                        }
                    },
                    "required": ["expression"],
                },
            },
        },
    )


__install__ = install
