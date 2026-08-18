"""
Core System Tools providing essential search, web fetching, time, math expression evaluation, and code execution
"""

import ast
import contextlib
import fnmatch
from html.parser import HTMLParser
import json
import math
import operator
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from statistics import mean, median, stdev, variance
from typing import Annotated, Any, Dict, List, Optional, Union

from aiohttp import web

g_ctx = None

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
# Web & URL fetching tools
# -----------------------------


class HTMLToMarkdownParser(HTMLParser):
    """Zero-dependency HTML to Markdown parser using Python standard library."""

    def __init__(self, base_url: str = ""):
        super().__init__()
        self.base_url = base_url
        self.result = []
        self.skip_tags = {
            "script",
            "style",
            "head",
            "svg",
            "noscript",
            "iframe",
            "canvas",
            "template",
            "nav",
            "footer",
            "aside",
        }
        self.skip_depth = 0
        self.current_href = None
        self.link_text = []
        self.in_pre = False

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        if tag_lower in self.skip_tags:
            self.skip_depth += 1
            return
        if self.skip_depth > 0:
            return

        attrs_dict = dict(attrs)

        if tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_lower[1])
            self.result.append(f"\n\n{'#' * level} ")
        elif tag_lower in ("p", "div", "section", "article"):
            self.result.append("\n\n")
        elif tag_lower == "blockquote":
            self.result.append("\n\n> ")
        elif tag_lower == "br":
            self.result.append("\n")
        elif tag_lower == "hr":
            self.result.append("\n\n---\n\n")
        elif tag_lower == "li":
            self.result.append("\n- ")
        elif tag_lower == "pre":
            self.in_pre = True
            self.result.append("\n```\n")
        elif tag_lower == "code" and not self.in_pre:
            self.result.append("`")
        elif tag_lower in ("b", "strong"):
            self.result.append("**")
        elif tag_lower in ("i", "em"):
            self.result.append("*")
        elif tag_lower == "a":
            self.current_href = attrs_dict.get("href")
            self.link_text = []
        elif tag_lower == "img":
            alt = attrs_dict.get("alt", "")
            src = attrs_dict.get("src", "")
            if src:
                full_src = urllib.parse.urljoin(self.base_url, src) if self.base_url else src
                self.result.append(f"![{alt}]({full_src})")
        elif tag_lower == "tr":
            self.result.append("\n| ")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in self.skip_tags:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth > 0:
            return

        if tag_lower == "pre":
            self.in_pre = False
            self.result.append("\n```\n")
        elif tag_lower == "code" and not self.in_pre:
            self.result.append("`")
        elif tag_lower in ("b", "strong"):
            self.result.append("**")
        elif tag_lower in ("i", "em"):
            self.result.append("*")
        elif tag_lower == "a":
            text = "".join(self.link_text).strip()
            if text and self.current_href:
                full_href = urllib.parse.urljoin(self.base_url, self.current_href) if self.base_url else self.current_href
                self.result.append(f"[{text}]({full_href})")
            elif text:
                self.result.append(text)
            self.current_href = None
            self.link_text = []
        elif tag_lower in ("td", "th"):
            self.result.append(" | ")
        elif tag_lower == "tr":
            self.result.append("\n")

    def handle_data(self, data):
        if self.skip_depth > 0:
            return
        if self.current_href is not None:
            self.link_text.append(data)
        else:
            if self.in_pre:
                self.result.append(data)
            else:
                self.result.append(re.sub(r"[ \t]+", " ", data))

    def get_markdown(self) -> str:
        text = "".join(self.result)
        # Collapse excessive newlines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def fetch_url(
    url: Annotated[str, "The HTTP/HTTPS URL to fetch content from"],
    max_length: Annotated[int, "Maximum character length of content to return (default: 20000)"] = 20000,
) -> str:
    """
    Fetch content from a URL via HTTP request and convert HTML to clean, structured Markdown.
    Non-HTML content (JSON, plain text) is returned as raw text.
    """
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    if g_ctx:
        g_ctx.dbg(f"fetch_url ({url})")

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            raw_bytes = response.read(2 * 1024 * 1024)  # Read at most 2MB
            charset = response.headers.get_content_charset() or "utf-8"
            raw_text = raw_bytes.decode(charset, errors="replace")

            if "html" in content_type or "<html" in raw_text[:500].lower() or "<!doctype html" in raw_text[:500].lower():
                parser = HTMLToMarkdownParser(base_url=url)
                parser.feed(raw_text)
                content = parser.get_markdown()
            else:
                content = raw_text.strip()

            if len(content) > max_length:
                remaining = len(content) - max_length
                return content[:max_length] + f"\n\n... [Truncated: {remaining} additional characters]"
            return content or "No content found on page."
    except Exception as e:
        return f"Error fetching URL '{url}': {e}"


# -----------------------------
# File search & grep tools
# -----------------------------

_IGNORED_SEARCH_DIRS = {
    ".git",
    ".venv",
    "venv",
    ".env",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "bin",
    "obj",
    "target",
    "vendor",
    ".next",
    ".nuxt",
    ".cache",
    ".tox",
}


def grep_search(
    query: Annotated[str, "Text or regular expression to search for across files"],
    path: Annotated[Optional[str], "Directory or file path to search (default is current working directory)"] = None,
    is_regex: Annotated[bool, "Whether to treat query as a regular expression (default: False)"] = False,
    case_sensitive: Annotated[bool, "Whether the search is case-sensitive (default: False)"] = False,
    file_pattern: Annotated[Optional[str], "Optional glob pattern to filter filenames (e.g. '*.py', '*.ts')"] = None,
    max_matches: Annotated[int, "Maximum number of matching lines to return (default: 50)"] = 50,
) -> str:
    """
    Search for exact text or regex patterns across files within a directory tree.
    Returns matched file paths, line numbers, and matching line content.
    """
    search_dir = path
    if not search_dir:
        if g_ctx and hasattr(g_ctx, "resolve_allowed_directories"):
            allowed = g_ctx.resolve_allowed_directories()
            search_dir = allowed[0] if allowed else os.getcwd()
        else:
            search_dir = os.getcwd()

    search_path = os.path.abspath(os.path.expanduser(search_dir))
    if not os.path.exists(search_path):
        return f"Error: Path '{search_dir}' does not exist."

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if is_regex else re.escape(query), flags)
    except re.error as e:
        return f"Error: Invalid regular expression '{query}': {e}"

    if g_ctx:
        g_ctx.dbg(f"grep_search ('{query}' in {search_path})")

    matches = []
    base_dir = search_path if os.path.isdir(search_path) else os.path.dirname(search_path)

    def search_file(file_path: str):
        try:
            with open(file_path, "rb") as f:
                header = f.read(1024)
                if b"\x00" in header:
                    return  # Skip binary file
                f.seek(0)
                line_no = 0
                for raw_line in f:
                    line_no += 1
                    try:
                        line = raw_line.decode("utf-8", errors="ignore")
                    except Exception:
                        continue
                    if pattern.search(line):
                        rel_path = os.path.relpath(file_path, base_dir)
                        matches.append(f"{rel_path}:{line_no}: {line.rstrip()}")
                        if len(matches) >= max_matches:
                            return
        except (OSError, PermissionError):
            pass

    if os.path.isfile(search_path):
        search_file(search_path)
    else:
        for root, dirs, files in os.walk(search_path):
            # Prune ignored directories
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _IGNORED_SEARCH_DIRS]
            for file in sorted(files):
                if file_pattern and not fnmatch.fnmatch(file, file_pattern):
                    continue
                file_path = os.path.join(root, file)
                search_file(file_path)
                if len(matches) >= max_matches:
                    break
            if len(matches) >= max_matches:
                break

    if not matches:
        return f"No matches found for '{query}'."

    output = "\n".join(matches)
    if len(matches) >= max_matches:
        output += f"\n\n... [Capped at {max_matches} matches]"
    return output


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


# JSON -> JSON Schema generation --------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPTS_DIR = os.path.join(_DIR, "prompts")

# ```json ... ``` block returned by the schema prompt
CODE_BLOCK_RE = re.compile(r"```([^\n`]*)\n(.*?)[ \t]*```", re.DOTALL)

SCHEMA_SUFFIX = ".ui.json"


def read_prompt(name: str) -> str:
    with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


def json_stem(name: str) -> str:
    """invoice.json / invoice.ui.json -> invoice"""
    base = os.path.basename(name or "data.json")
    if base.endswith(SCHEMA_SUFFIX):
        return base[: -len(SCHEMA_SUFFIX)]
    return os.path.splitext(base)[0] or "data"


def first_code_block(answer: str) -> str:
    blocks = CODE_BLOCK_RE.findall(answer or "")
    return (blocks[0][1] if blocks else (answer or "")).strip()


def install(ctx):
    global g_ctx
    g_ctx = ctx
    group = "core_tools"
    # Examples of registering tools using automatic definition generation
    ctx.register_tool(fetch_url, group=group)
    ctx.register_tool(grep_search, group=group)
    ctx.register_tool(get_current_time, group=group)
    ctx.register_tool(calc, group=group)
    ctx.register_tool(run_python, group=group)
    ctx.register_tool(run_typescript, group=group)
    ctx.register_tool(run_javascript, group=group)
    ctx.register_tool(run_csharp, group=group)

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

    # JSON -> typed classes / UI schema, used by the /code json tab and the pdf designer ------------

    def find_model(model_id: str):
        for provider in ctx.get_providers().values():
            models = getattr(provider, "models", None) or {}
            for model in models.values() if isinstance(models, dict) else models:
                if isinstance(model, dict) and model_id in (model.get("id"), model.get("name")):
                    return model
        return None

    def assert_text_model(model_id: str):
        """Codegen needs a model that answers with text, not an image/audio generation model"""
        model = find_model(model_id)
        if not model:
            return  # unknown to us (custom/proxied model), let the provider decide
        output = (model.get("modalities") or {}).get("output")
        if output and "text" not in output:
            raise Exception(f"'{model_id}' outputs {'/'.join(output)}, not text. Select a text model.")

    async def ask_model(ctx_user, model: str, system_prompt: str, user_message: str) -> dict:
        response = await ctx.chat_completion(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            },
            context={"tools": "none", "nohistory": True, "nostore": True, "user": ctx_user},
        )
        answer = (response.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if not answer.strip():
            raise Exception("The model returned an empty response")
        return {"answer": answer, "usage": response.get("usage")}

    def read_json_body(body: dict):
        """Every codegen request carries the JSON document itself, so nothing touches the filesystem"""
        name = body.get("name") or body.get("path") or "data.json"
        content = body.get("content")
        model = body.get("model")
        if not model:
            raise Exception("No model selected")
        if not content or not content.strip():
            raise Exception("No JSON content supplied")
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            raise Exception(f"'{os.path.basename(name)}' is not valid JSON: {e}") from None
        assert_text_model(model)
        return name, content, model

    async def generate_ui_schema(request):
        """Turn a JSON document into a JSON Schema that JsonSchemaForm renders"""
        user = ctx.assert_username(request)
        body = await request.json()
        name, content, model = read_json_body(body)

        out_name = json_stem(name) + SCHEMA_SUFFIX
        result = await ask_model(
            user,
            model,
            read_prompt("generate-ui-schema.md"),
            f"Data file: `{os.path.basename(name)}`\nSchema file: `{out_name}`\n\n```json\n{content}\n```",
        )
        schema_text = first_code_block(result["answer"])
        try:
            schema = json.loads(schema_text)
        except json.JSONDecodeError as e:
            raise Exception(f"The model did not return valid JSON Schema: {e}") from None
        if not isinstance(schema, dict) or "properties" not in schema:
            raise Exception("The model's schema has no 'properties'")

        return web.json_response(
            {
                "path": out_name,
                "content": json.dumps(schema, indent=2) + "\n",
                "model": model,
                "usage": result["usage"],
            }
        )

    ctx.add_post("schema", generate_ui_schema)

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
