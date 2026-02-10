import asyncio
import json
import os
import subprocess
import shutil
import time
from collections import deque

from aiohttp import web

SCRIPTS_DIR = None
PROFILE_DIR = None
STATE_FILE = None
AGENT_BROWSER_USER_AGENT = os.getenv(
    "AGENT_BROWSER_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/28.0.1500.52 Safari/537.36",
)
DEBUG_LOG = deque(maxlen=200)
DEBUG_LOG_COUNTER = 0


def install(ctx):
    global SCRIPTS_DIR, PROFILE_DIR, STATE_FILE

    # Check for agent-browser binary
    if not shutil.which("agent-browser"):
        ctx.log("agent-browser not found. See https://agent-browser.dev/installation to use the browser extension.")
        ctx.disabled = True
        return

    user_path = ctx.get_user_path()
    SCRIPTS_DIR = os.path.join(user_path, "browser", "scripts")
    PROFILE_DIR = os.path.join(user_path, "browser", "profile")
    STATE_FILE = os.path.join(user_path, "browser", "state.json")

    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)

    _browser_env = {"AGENT_BROWSER_PROFILE": PROFILE_DIR, "AGENT_BROWSER_USER_AGENT": AGENT_BROWSER_USER_AGENT}

    def _add_debug_log(cmd_str, result, duration):
        global DEBUG_LOG_COUNTER
        DEBUG_LOG_COUNTER += 1
        DEBUG_LOG.append(
            {
                "id": DEBUG_LOG_COUNTER,
                "ts": time.time(),
                "cmd": cmd_str,
                "rc": result.get("returncode", -1),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", result.get("error", "")),
                "ok": result.get("success", False),
                "ms": round(duration * 1000),
            }
        )

    def run_browser_cmd(*args, timeout=30, env=None):
        """Run agent-browser command and return output."""
        cmd = ["agent-browser"] + list(args)
        cmd_str = " ".join(cmd)
        t0 = time.monotonic()
        try:
            ctx.dbg(f"Running: {cmd_str}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, **env} if env else None,
            )
            ret = {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            ret = {"success": False, "error": "Command timed out"}
        except Exception as e:
            ret = {"success": False, "error": str(e)}
        _add_debug_log(cmd_str, ret, time.monotonic() - t0)
        return ret

    async def run_browser_cmd_async(*args, timeout=30, env=None):
        """Run agent-browser command asynchronously."""
        cmd = ["agent-browser"] + list(args)
        cmd_str = " ".join(cmd)
        t0 = time.monotonic()
        try:
            ctx.dbg(f"Running: {cmd_str}")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **env} if env else None,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            ret = {
                "success": proc.returncode == 0,
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
                "returncode": proc.returncode,
            }
        except asyncio.TimeoutError:
            proc.kill()
            ret = {"success": False, "error": "Command timed out"}
        except Exception as e:
            ret = {"success": False, "error": str(e)}
        _add_debug_log(cmd_str, ret, time.monotonic() - t0)
        return ret

    # =========================================================================
    # Status & Screenshot Endpoints
    # =========================================================================

    async def get_status(req):
        """Get current browser status including URL and title."""
        url_result = await run_browser_cmd_async("get", "url")
        title_result = await run_browser_cmd_async("get", "title")

        if not url_result["success"] and "no active" in url_result.get("stderr", "").lower():
            return web.json_response({"running": False, "url": None, "title": None})

        return web.json_response(
            {
                "running": url_result["success"],
                "url": url_result["stdout"].strip() if url_result["success"] else None,
                "title": title_result["stdout"].strip() if title_result["success"] else None,
            }
        )

    ctx.add_get("/browser/status", get_status)

    async def get_debug_log(req):
        """Return the debug log entries. Supports ?since=<id> to get only new entries."""
        since = int(req.query.get("since", 0))
        entries = [e for e in DEBUG_LOG if e["id"] > since]
        return web.json_response({"entries": entries})

    ctx.add_get("/browser/debug-log", get_debug_log)

    async def clear_debug_log(request):
        """Clear the debug log."""
        global DEBUG_LOG_COUNTER
        DEBUG_LOG.clear()
        DEBUG_LOG_COUNTER = 0
        return web.json_response({"success": True})

    ctx.add_delete("/browser/debug-log", clear_debug_log)

    async def get_screenshot(req):
        """Capture and return current screenshot."""
        screenshot_path = os.path.join(ctx.get_user_path(user=ctx.get_username(req)), "browser", "screenshot.png")

        result = await run_browser_cmd_async("screenshot", screenshot_path)
        if not result["success"]:
            return web.json_response({"error": result.get("stderr", "Screenshot failed")}, status=500)

        if os.path.exists(screenshot_path):
            return web.FileResponse(screenshot_path, headers={"Content-Type": "image/png", "Cache-Control": "no-cache"})
        return web.json_response({"error": "Screenshot file not found"}, status=500)

    ctx.add_get("/browser/screenshot", get_screenshot)

    async def get_snapshot(req):
        """Get interactive elements snapshot with refs."""
        include_cursor = req.query.get("cursor", "false") == "true"
        args = ["snapshot", "-i", "--json"]
        if include_cursor:
            args.append("-C")

        result = await run_browser_cmd_async(*args)
        if not result["success"]:
            return web.json_response({"error": result.get("stderr", "Snapshot failed")}, status=500)

        try:
            parsed = json.loads(result["stdout"]) if result["stdout"].strip() else {}
            # agent-browser --json returns {"success":true,"data":{"refs":{...},"snapshot":"..."}}
            if isinstance(parsed, dict) and "data" in parsed and "refs" in parsed["data"]:
                refs = parsed["data"]["refs"]
                elements = [
                    {"ref": f"@{key}", "desc": f'{val.get("role", "")} "{val.get("name", "")}"'.strip()}
                    for key, val in sorted(refs.items(), key=lambda x: int(x[0][1:]) if x[0][1:].isdigit() else 0)
                ]
            elif isinstance(parsed, list):
                elements = parsed
            else:
                elements = []
        except json.JSONDecodeError:
            # Return raw text if not JSON
            elements = result["stdout"].strip().split("\n")

        return web.json_response({"elements": elements})

    ctx.add_get("/browser/snapshot", get_snapshot)

    # =========================================================================
    # Navigation Endpoints
    # =========================================================================

    async def browser_open(req):
        """Navigate to URL."""
        data = await req.json()
        url = data.get("url")
        ctx.log(f"browser_open: Opening URL: {url}")
        if not url:
            return web.json_response({"error": "URL required"}, status=400)

        result = await run_browser_cmd_async("open", url, timeout=60, env=_browser_env)
        ctx.log(
            f"browser_open: Open result: success={result['success']}, stdout={result.get('stdout', '')[:100]}, stderr={result.get('stderr', '')[:100]}"
        )
        if not result["success"]:
            return web.json_response({"success": False, "error": result.get("stderr", "Failed to open URL")})

        # Wait for page to fully load
        wait_result = await run_browser_cmd_async("wait", "--load", "networkidle", timeout=60)
        ctx.log(f"browser_open: Wait result: success={wait_result['success']}")

        return web.json_response(
            {
                "success": wait_result["success"],
                "error": wait_result.get("stderr") if not wait_result["success"] else None,
            }
        )

    ctx.add_post("/browser/open", browser_open)

    async def browser_close(req):
        """Close browser session and save state."""
        # Save state before closing
        await run_browser_cmd_async("state", "save", STATE_FILE)
        result = await run_browser_cmd_async("close")
        return web.json_response({"success": result["success"]})

    ctx.add_post("/browser/close", browser_close)

    async def browser_back(req):
        """Navigate back."""
        result = await run_browser_cmd_async("back")
        return web.json_response({"success": result["success"]})

    ctx.add_post("/browser/back", browser_back)

    async def browser_forward(req):
        """Navigate forward."""
        result = await run_browser_cmd_async("forward")
        return web.json_response({"success": result["success"]})

    ctx.add_post("/browser/forward", browser_forward)

    async def browser_reload(req):
        """Reload page."""
        result = await run_browser_cmd_async("reload")
        return web.json_response({"success": result["success"]})

    ctx.add_post("/browser/reload", browser_reload)

    # =========================================================================
    # Interaction Endpoints
    # =========================================================================

    async def browser_click(req):
        """Click at coordinates or element ref."""
        data = await req.json()

        if "ref" in data:
            # Click by ref (e.g., "@e1")
            result = await run_browser_cmd_async("click", data["ref"])
        elif "x" in data and "y" in data:
            # Click by coordinates
            result = await run_browser_cmd_async("mouse", "move", str(data["x"]), str(data["y"]))
            if result["success"]:
                result = await run_browser_cmd_async("mouse", "down", "left")
                if result["success"]:
                    result = await run_browser_cmd_async("mouse", "up", "left")
        else:
            return web.json_response({"error": "Need ref or x,y coordinates"}, status=400)

        return web.json_response({"success": result["success"]})

    ctx.add_post("/browser/click", browser_click)

    async def browser_type(req):
        """Type text into element by ref, or press keys into focused element."""
        data = await req.json()
        text = data.get("text", "")
        ref = data.get("ref")

        if ref:
            result = await run_browser_cmd_async("type", ref, text)
        else:
            # No selector — press each character into the focused element
            result = {"success": True}
            for ch in text:
                result = await run_browser_cmd_async("press", ch)
                if not result["success"]:
                    break

        return web.json_response({"success": result["success"]})

    ctx.add_post("/browser/type", browser_type)

    async def browser_press(req):
        """Press key."""
        data = await req.json()
        key = data.get("key", "Enter")
        result = await run_browser_cmd_async("press", key)
        return web.json_response({"success": result["success"]})

    ctx.add_post("/browser/press", browser_press)

    async def browser_scroll(req):
        """Scroll page."""
        data = await req.json()
        direction = data.get("direction", "down")
        amount = data.get("amount", 300)
        result = await run_browser_cmd_async("scroll", direction, str(amount))
        return web.json_response({"success": result["success"]})

    ctx.add_post("/browser/scroll", browser_scroll)

    # =========================================================================
    # Session State Endpoints
    # =========================================================================

    async def save_state(req):
        """Save browser session state."""
        result = await run_browser_cmd_async("state", "save", STATE_FILE)
        return web.json_response({"success": result["success"], "path": STATE_FILE if result["success"] else None})

    ctx.add_post("/browser/state/save", save_state)

    async def load_state(req):
        """Load browser session state."""
        if not os.path.exists(STATE_FILE):
            return web.json_response({"error": "No saved state found"}, status=404)

        result = await run_browser_cmd_async("state", "load", STATE_FILE)
        return web.json_response({"success": result["success"]})

    ctx.add_post("/browser/state/load", load_state)

    async def list_sessions(req):
        """List active browser sessions."""
        result = await run_browser_cmd_async("session", "list")
        sessions = result["stdout"].strip().split("\n") if result["success"] and result["stdout"].strip() else []
        return web.json_response({"sessions": sessions})

    ctx.add_get("/browser/sessions", list_sessions)

    # =========================================================================
    # Script Management Endpoints
    # =========================================================================

    async def list_scripts(req):
        """List automation scripts."""
        scripts = []
        if os.path.exists(SCRIPTS_DIR):
            for name in os.listdir(SCRIPTS_DIR):
                if name.endswith(".sh"):
                    path = os.path.join(SCRIPTS_DIR, name)
                    scripts.append(
                        {"name": name, "path": path, "size": os.path.getsize(path), "modified": os.path.getmtime(path)}
                    )
        return web.json_response({"scripts": scripts})

    ctx.add_get("/browser/scripts", list_scripts)

    async def get_script(req):
        """Get script content."""
        name = req.match_info["name"]
        if not name.endswith(".sh"):
            name += ".sh"
        path = os.path.join(SCRIPTS_DIR, name)

        if not os.path.exists(path):
            return web.json_response({"error": "Script not found"}, status=404)

        with open(path) as f:
            content = f.read()

        return web.json_response({"name": name, "content": content})

    ctx.add_get("/browser/scripts/{name}", get_script)

    async def save_script(req):
        """Create or update script."""
        data = await req.json()
        name = data.get("name", "").strip()
        content = data.get("content", "")

        if not name:
            return web.json_response({"error": "Script name required"}, status=400)

        if not name.endswith(".sh"):
            name += ".sh"

        # Sanitize name
        name = os.path.basename(name)
        path = os.path.join(SCRIPTS_DIR, name)

        with open(path, "w") as f:
            f.write(content)

        os.chmod(path, 0o755)

        return web.json_response({"success": True, "name": name, "path": path})

    ctx.add_post("/browser/scripts", save_script)

    async def delete_script(req):
        """Delete script."""
        name = req.match_info["name"]
        if not name.endswith(".sh"):
            name += ".sh"
        path = os.path.join(SCRIPTS_DIR, os.path.basename(name))

        if not os.path.exists(path):
            return web.json_response({"error": "Script not found"}, status=404)

        os.remove(path)
        return web.json_response({"success": True})

    ctx.add_delete("/browser/scripts/{name}", delete_script)

    async def run_script(req):
        """Execute a script."""
        name = req.match_info["name"]
        if not name.endswith(".sh"):
            name += ".sh"
        path = os.path.join(SCRIPTS_DIR, os.path.basename(name))

        if not os.path.exists(path):
            return web.json_response({"error": "Script not found"}, status=404)

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "AGENT_BROWSER_SESSION": "default"},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            result = {
                "success": proc.returncode == 0,
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
                "returncode": proc.returncode,
            }
            _add_debug_log(f"bash {name}", result, time.monotonic() - t0)
            return web.json_response(result)
        except asyncio.TimeoutError:
            proc.kill()
            result = {"success": False, "error": "Script execution timed out", "returncode": -1, "stdout": "", "stderr": "Script execution timed out"}
            _add_debug_log(f"bash {name}", result, time.monotonic() - t0)
            return web.json_response({"error": "Script execution timed out"}, status=500)
        except Exception as e:
            result = {"success": False, "error": str(e), "returncode": -1, "stdout": "", "stderr": str(e)}
            _add_debug_log(f"bash {name}", result, time.monotonic() - t0)
            return web.json_response({"error": str(e)}, status=500)

    ctx.add_post("/browser/scripts/{name}/run", run_script)

    # =========================================================================
    # AI Script Generation
    # =========================================================================

    async def generate_script(req):
        """Generate or modify a script from prompt using AI."""
        data = await req.json()
        prompt = data.get("prompt", "")
        name = data.get("name", "generated-script.sh")
        existing_script = data.get("existing_script", "")

        if not prompt:
            return web.json_response({"error": "Prompt required"}, status=400)

        system_prompt = """You are an expert at browser automation using the agent-browser CLI tool.
Generate a bash script that accomplishes the user's task.

Key commands:
- agent-browser open <url> - Navigate to URL
- agent-browser snapshot -i - Get interactive elements with refs (@e1, @e2, etc.)
- agent-browser click @e1 - Click element by ref
- agent-browser fill @e1 "text" - Fill input field
- agent-browser type @e1 "text" - Type without clearing
- agent-browser press Enter - Press key
- agent-browser wait --load networkidle - Wait for page load
- agent-browser wait @e1 - Wait for element
- agent-browser get text @e1 - Get element text
- agent-browser screenshot output.png - Take screenshot

Always:
1. Start with #!/bin/bash and set -euo pipefail
2. Use snapshot -i after navigation to get element refs
3. Wait for page loads with --load networkidle
4. Add comments explaining each step

Output ONLY the bash script, no explanations."""

        if existing_script.strip():
            user_message = f"Here is an existing browser automation script:\n\n```bash\n{existing_script}\n```\n\nModify this script to: {prompt}\n\nOutput the complete updated script."
        else:
            user_message = f"Create a browser automation script that: {prompt}"

        chat_request = {
            "model": ctx.config.get("defaults", {}).get("text", {}).get("model", "MiniMax-M2.1"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        try:
            response = await ctx.chat_completion(chat_request, context={"tools": "none"})
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Clean up the response - extract just the script
            if "```bash" in content:
                content = content.split("```bash")[1].split("```")[0]
            elif "```sh" in content:
                content = content.split("```sh")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            content = content.strip()
            if not content.startswith("#!/bin/bash"):
                content = "#!/bin/bash\nset -euo pipefail\n\n" + content

            return web.json_response({"success": True, "name": name, "content": content})
        except Exception as e:
            ctx.err("generate_script", e)
            return web.json_response({"error": str(e)}, status=500)

    ctx.add_post("/browser/scripts/generate", generate_script)

    ctx.add_importmaps({"xterm": f"{ctx.ext_prefix}/xterm-esm.js"})
    ctx.add_index_footer(
        f"""
        <link rel="stylesheet" href="{ctx.ext_prefix}/xterm.css">
        <script src="{ctx.ext_prefix}/shell.js"></script>
        """
    )


__install__ = install
