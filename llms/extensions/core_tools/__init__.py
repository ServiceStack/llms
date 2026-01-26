"""
Core System Tools providing essential file operations, memory persistence, math expression evaluation, and code execution
"""

import ast
import contextlib
import glob
import json
import math
import operator
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from statistics import mean, median, stdev, variance
from typing import Any, Dict, List, Optional

from aiohttp import web

g_ctx = None

# -----------------------------
# In-memory storage (replace later)
# -----------------------------

_MEMORY_STORE: Dict[str, Any] = {}
_SEMANTIC_STORE: List[Dict[str, Any]] = []  # {id, text, metadata}


# -----------------------------
# Memory tools
# -----------------------------


def memory_read(key: str) -> Any:
    """Read a value from persistent memory."""
    return _MEMORY_STORE.get(key)


def memory_write(key: str, value: Any) -> bool:
    """Write a value to persistent memory."""
    _MEMORY_STORE[key] = value
    return True


# -----------------------------
# Path safety helpers
# -----------------------------

# Limit tools to only access files and folders within LLMS_BASE_DIR if specified, otherwise the current working directory
_BASE_DIR = os.environ.get("LLMS_BASE_DIR") or os.path.realpath(os.getcwd())


def _resolve_safe_path(path: str) -> str:
    """
    Resolve a path and ensure it stays within the current working directory.
    Raises ValueError if the path escapes the base directory.
    """
    resolved = os.path.realpath(os.path.join(_BASE_DIR, path))
    if not resolved.startswith(_BASE_DIR + os.sep) and resolved != _BASE_DIR:
        raise ValueError("Access denied: path is outside the working directory")
    return resolved


# -----------------------------
# Semantic search (placeholder)
# -----------------------------


def semantic_search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Naive semantic search placeholder.
    Replace with embeddings + vector DB.
    """
    results = []
    for item in _SEMANTIC_STORE:
        if query.lower() in item["text"].lower():
            results.append(item)
    return results[:top_k]


# -----------------------------
# File system tools (restricted to CWD)
# -----------------------------


def read_file(path: str) -> str:
    """Read a text file from disk within the current working directory."""
    safe_path = _resolve_safe_path(path)
    with open(safe_path, encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> bool:
    """Write text to a file within the current working directory (overwrites)."""
    safe_path = _resolve_safe_path(path)
    os.makedirs(os.path.dirname(safe_path) or _BASE_DIR, exist_ok=True)
    with open(safe_path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def list_directory(path: str) -> str:
    """List directory contents"""
    safe_path = _resolve_safe_path(path)
    if not os.path.exists(safe_path):
        return f"Error: Path not found: {path}"

    entries = []
    try:
        for entry in os.scandir(safe_path):
            stat = entry.stat()
            entries.append(
                {
                    "name": "/" + entry.name if entry.is_dir() else entry.name,
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
            )
        return json.dumps({"path": os.path.relpath(safe_path, _BASE_DIR), "entries": entries}, indent=2)
    except Exception as e:
        return f"Error listing directory: {e}"


def glob_paths(
    pattern: str,
    extensions: Optional[List[str]] = None,
    sort_by: str = "path",  # "path" | "modified" | "size"
    max_results: int = 100,
) -> Dict[str, List[Dict[str, str]]]:
    """
    Find files and directories matching a glob pattern
    """
    if sort_by not in {"path", "modified", "size"}:
        raise ValueError("sort_by must be one of: path, modified, size")

    safe_pattern = _resolve_safe_path(pattern)

    results = []

    for path in glob.glob(safe_pattern, recursive=True):
        resolved = os.path.realpath(path)

        # Enforce CWD restriction (important for symlinks)
        if not resolved.startswith(_BASE_DIR):
            continue

        is_dir = os.path.isdir(resolved)

        # Extension filtering (files only)
        if extensions and not is_dir:
            ext = os.path.splitext(resolved)[1].lower().lstrip(".")
            if ext not in {e.lower().lstrip(".") for e in extensions}:
                continue

        stat = os.stat(resolved)

        results.append(
            {
                "path": os.path.relpath(resolved, _BASE_DIR),
                "type": "directory" if is_dir else "file",
                "size_bytes": stat.st_size,
                "modified_time": stat.st_mtime,
            }
        )

        if len(results) >= max_results:
            break

    # Sorting
    if sort_by == "path":
        results.sort(key=lambda x: x["path"])
    elif sort_by == "modified":
        results.sort(key=lambda x: x["modified_time"], reverse=True)
    elif sort_by == "size":
        results.sort(key=lambda x: x["size_bytes"], reverse=True)

    return {"pattern": pattern, "count": len(results), "results": results}


# -----------------------------
# Expression evaluation tools
# -----------------------------


def get_calculator_functions():
    # 2. Define allowed math functions and constants
    allowed_functions = {
        "mod": operator.mod,
        "mean": mean,
        "median": median,
        "stdev": stdev,
        "variance": variance,
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "round": round,
    }
    allowed_functions.update(
        {name: getattr(math, name) for name in dir(math) if not name.startswith("_") and name not in allowed_functions}
    )
    return allowed_functions


def calc(expression: str) -> str:
    """Evaluate a mathematical expression with boolean operations"""
    # 1. Define allowed operators
    operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.Mod: operator.mod,
        # Comparison operators
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        # Boolean operators
        ast.And: operator.and_,
        ast.Or: operator.or_,
        ast.Not: operator.not_,
    }

    # 2. Define allowed math functions and constants
    allowed_functions = get_calculator_functions()

    def eval_node(node, context=None):
        if context is None:
            context = {}

        if isinstance(node, ast.Constant):  # Numbers and booleans
            return node.value
        elif isinstance(node, ast.BinOp):  # Binary Ops (1 + 2)
            return operators[type(node.op)](eval_node(node.left, context), eval_node(node.right, context))
        elif isinstance(node, ast.UnaryOp):  # Unary Ops (-5, not True)
            return operators[type(node.op)](eval_node(node.operand, context))
        elif isinstance(node, ast.Compare):  # Comparison (5 > 3)
            left = eval_node(node.left, context)
            for op, comparator in zip(node.ops, node.comparators):
                right = eval_node(comparator, context)
                if not operators[type(op)](left, right):
                    return False
                left = right
            return True
        elif isinstance(node, ast.BoolOp):  # Boolean operations (True and False, True or False)
            if isinstance(node.op, ast.And):
                # Short-circuit evaluation for 'and'
                result = True
                for value in node.values:
                    result = eval_node(value, context)
                    if not result:
                        return False
                return result
            elif isinstance(node.op, ast.Or):
                # Short-circuit evaluation for 'or'
                for value in node.values:
                    result = eval_node(value, context)
                    if result:
                        return True
                return False
        elif isinstance(node, ast.Call):  # Function calls (sqrt(16))
            func_name = node.func.id
            if func_name in allowed_functions:
                args = [eval_node(arg, context) for arg in node.args]
                return allowed_functions[func_name](*args)
            if func_name == "range":
                args = [eval_node(arg, context) for arg in node.args]
                return range(*args)
            raise NameError(f"Function '{func_name}' is not allowed.")
        elif isinstance(node, ast.Name):  # Constants (pi, e, True, False) or context variables
            if node.id in context:
                return context[node.id]
            if node.id in allowed_functions:
                return allowed_functions[node.id]
            elif node.id in ("True", "False"):
                return node.id == "True"
            raise NameError(f"Variable '{node.id}' is not defined.")
        elif isinstance(node, ast.List):  # List literals [1, 2, 3]
            return [eval_node(item, context) for item in node.elts]
        elif isinstance(node, ast.ListComp):  # List comprehensions [x*2 for x in [1,2,3]]
            result = []
            generators = node.generators
            if len(generators) != 1:
                raise ValueError("Only single-generator list comprehensions are supported")
            gen = generators[0]
            if not isinstance(gen.target, ast.Name):
                raise ValueError("Only simple name targets in list comprehensions are supported")

            target_name = gen.target.id
            iterable = eval_node(gen.iter, context)

            for item in iterable:
                new_context = context.copy()
                new_context[target_name] = item

                # Check ifs
                include = True
                for if_node in gen.ifs:
                    if not eval_node(if_node, new_context):
                        include = False
                        break

                if include:
                    result.append(eval_node(node.elt, new_context))
            return result
        else:
            raise TypeError(f"Unsupported operation: {type(node).__name__}")

    # Replace XOR with power
    expression = expression.replace("^", "**")

    # Parse and evaluate
    node = ast.parse(expression, mode="eval").body
    ret = eval_node(node)
    g_ctx.dbg(f"calc ({expression}) = {ret}")
    return ret


# -----------------------------
# code execution tools
# -----------------------------

mem_limit = 8589934592  # Max virtual memory 8GB
cpu_time_limit = 5  # Max CPU time 5 seconds
resource_limits = f"ulimit -t {cpu_time_limit}; ulimit -v {mem_limit};"


def run_python(code: str) -> Dict[str, Any]:
    """
    Execute Python code in a temporary sandboxed environment.
    Uses ulimit for resource restriction and runs in a temporary directory.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = os.path.join(temp_dir, "script.py")

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        cmd = f"{resource_limits} {sys.executable} script.py"

        run_as = os.environ.get("LLMS_RUN_AS")
        if run_as:
            # Grant access to temp_dir
            with contextlib.suppress(Exception):
                os.chmod(temp_dir, 0o777)
            cmd = f"sudo -u {run_as} bash -c '{cmd}'"

        try:
            # Run with restricted environment
            # We keep PATH to find basic tools if needed, but remove sensitive vars
            clean_env = {"PATH": os.environ.get("PATH", "")}

            g_ctx.dbg(f"run_python ({temp_dir}): {cmd}\n{code}")
            result = subprocess.run(
                ["bash", "-c", cmd], cwd=temp_dir, env=clean_env, capture_output=True, text=True, timeout=10
            )
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Execution timed out", "returncode": -1}
        except Exception as e:
            return {"stdout": "", "stderr": f"Error: {e}", "returncode": -1}


def run_javascript(code: str) -> Dict[str, Any]:
    """
    Execute JavaScript code in a temporary sandboxed environment using bun or node.
    """
    # Check for available runtime
    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        return {"stdout": "", "stderr": "Error: Neither 'bun' nor 'node' is available on the system.", "returncode": -1}

    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = os.path.join(temp_dir, "script.js")

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        cmd = f"{resource_limits} {runtime} script.js"

        run_as = os.environ.get("LLMS_RUN_AS")
        if run_as:
            with contextlib.suppress(Exception):
                os.chmod(temp_dir, 0o777)
            cmd = f"sudo -u {run_as} bash -c '{cmd}'"

        try:
            # Run with restricted environment
            clean_env = {"PATH": os.environ.get("PATH", "")}

            g_ctx.dbg(f"run_javascript ({temp_dir}): {cmd}\n{code}")
            result = subprocess.run(
                ["bash", "-c", cmd], cwd=temp_dir, env=clean_env, capture_output=True, text=True, timeout=10
            )
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Execution timed out", "returncode": -1}
        except Exception as e:
            return {"stdout": "", "stderr": f"Error: {e}", "returncode": -1}


def run_typescript(code: str) -> Dict[str, Any]:
    """
    Execute TypeScript code in a temporary sandboxed environment using bun or node.
    """
    # Check for available runtime
    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        return {"stdout": "", "stderr": "Error: Neither 'bun' nor 'node' is available on the system.", "returncode": -1}

    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = os.path.join(temp_dir, "script.ts")

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        cmd = f"{resource_limits} {runtime} script.ts"

        run_as = os.environ.get("LLMS_RUN_AS")
        if run_as:
            with contextlib.suppress(Exception):
                os.chmod(temp_dir, 0o777)
            cmd = f"sudo -u {run_as} bash -c '{cmd}'"

        try:
            # Run with restricted environment
            clean_env = {"PATH": os.environ.get("PATH", "")}

            g_ctx.dbg(f"run_typescript ({temp_dir}): {cmd}\n{code}")
            result = subprocess.run(
                ["bash", "-c", cmd], cwd=temp_dir, env=clean_env, capture_output=True, text=True, timeout=10
            )
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Execution timed out", "returncode": -1}
        except Exception as e:
            return {"stdout": "", "stderr": f"Error: {e}", "returncode": -1}


def run_csharp(code: str) -> Dict[str, Any]:
    """
    Execute C# code in a temporary sandboxed environment using dotnet.
    """
    # Check for available runtime
    runtime = shutil.which("dotnet")
    if not runtime:
        return {"stdout": "", "stderr": "Error: 'dotnet' is not available on the system.", "returncode": -1}

    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = os.path.join(temp_dir, "script.cs")

        # Ensure we just have the code, user might pass it without wrapping class if it's top-level statements
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        # Note: 'dotnet run script.cs' is the command as per user request for .NET 10
        cmd = f"{resource_limits} {runtime} run script.cs"

        run_as = os.environ.get("LLMS_RUN_AS")
        if run_as:
            with contextlib.suppress(Exception):
                os.chmod(temp_dir, 0o777)
            # For dotnet, we need to set HOME and DOTNET_CLI_HOME to temp_dir for write access
            cmd = f"sudo -u {run_as} env HOME={temp_dir} DOTNET_CLI_HOME={temp_dir} bash -c '{cmd}'"

        try:
            # Run with restricted environment
            clean_env = {"PATH": os.environ.get("PATH", "")}

            # Dotnet might need some ENV vars to work correctly, usually DOTNET_CLI_HOME or similar if strictly sandboxed
            # But we are keeping PATH, hopefully commonly needed vars are there or default works.
            # We might want to pass more env vars if it fails.
            g_ctx.dbg(f"run_csharp ({temp_dir}): {cmd}\n{code}")
            result = subprocess.run(
                ["bash", "-c", cmd], cwd=temp_dir, env=clean_env, capture_output=True, text=True, timeout=10
            )
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Execution timed out", "returncode": -1}
        except Exception as e:
            return {"stdout": "", "stderr": f"Error: {e}", "returncode": -1}


# -----------------------------
# Time tool
# -----------------------------


def get_current_time(tz_name: Optional[str] = None) -> str:
    """
    Get current time in ISO-8601 format.

    Args:
        tz_name: Optional timezone name (e.g. 'America/New_York'). Defaults to UTC.
    """
    if tz_name:
        try:
            try:
                from zoneinfo import ZoneInfo
            except ImportError:
                from backports.zoneinfo import ZoneInfo

            tz = ZoneInfo(tz_name)
        except Exception:
            return f"Error: Invalid timezone '{tz_name}'"
    else:
        tz = timezone.utc

    return datetime.now(tz).isoformat()


def install(ctx):
    global g_ctx
    g_ctx = ctx
    group = "core_tools"
    # Examples of registering tools using automatic definition generation
    ctx.register_tool(memory_read, group=group)
    ctx.register_tool(memory_write, group=group)
    # ctx.register_tool(semantic_search) # TODO: implement
    ctx.register_tool(read_file, group=group)
    ctx.register_tool(write_file, group=group)
    ctx.register_tool(list_directory, group=group)
    ctx.register_tool(glob_paths, group=group)
    ctx.register_tool(calc, group=group)
    ctx.register_tool(run_python, group=group)
    ctx.register_tool(run_typescript, group=group)
    ctx.register_tool(run_javascript, group=group)
    ctx.register_tool(run_csharp, group=group)
    ctx.register_tool(get_current_time, group=group)

    def exec_language(language: str, code: str) -> Dict[str, Any]:
        if language == "python":
            return run_python(code)
        elif language == "typescript":
            return run_typescript(code)
        elif language == "javascript":
            return run_javascript(code)
        elif language == "csharp":
            return run_csharp(code)
        else:
            return {"stdout": "", "stderr": "Error: Invalid language", "returncode": -1}

    async def run_code(request):
        language = request.match_info["language"]
        code = await request.text()
        try:
            result = exec_language(language, code)
        except Exception as e:
            result = {"stdout": "", "stderr": str(e), "returncode": -1}
        return web.json_response(result)

    ctx.add_post("code/{language}/run", run_code)

    async def get_calculator_features(request):
        operators = ["+", "-", "*", "/", "%", "^", "==", "!=", "<", "<=", ">", ">=", "and", "or", "not"]
        operators = [f" {op} " for op in operators]
        constants = ["pi", "e", "inf", "tau", "nan"]
        functions = [f for f in get_calculator_functions() if f not in constants]
        return web.json_response(
            {
                "numbers": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
                "constants": constants,
                "operators": operators,
                "functions": sorted(functions),
            }
        )

    ctx.add_get("calc", get_calculator_features)

    async def run_calc(request):
        code = await request.text()
        result = calc(code)
        return web.json_response({"result": result})

    ctx.add_post("calc", run_calc)

    ctx.add_index_footer(
        f"""
        <link rel="stylesheet" href="{ctx.ext_prefix}/codemirror/codemirror.css">
        <link rel="stylesheet" href="{ctx.ext_prefix}/codemirror/theme/mocha.css">
        <script src="{ctx.ext_prefix}/codemirror/codemirror.js"></script>
        <script src="{ctx.ext_prefix}/codemirror/mode/clike/clike.js"></script>
        <script src="{ctx.ext_prefix}/codemirror/mode/javascript/javascript.js"></script>
        <script src="{ctx.ext_prefix}/codemirror/mode/python/python.js"></script>
        <script src="{ctx.ext_prefix}/codemirror/addon/edit/matchbrackets.js"></script>
        <script src="{ctx.ext_prefix}/codemirror/addon/selection/active-line.js"></script>
        """
    )


__install__ = install
