import asyncio
import json
import os
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path

from aiohttp import web

AGENT_BROWSER_USER_AGENT = os.getenv(
    "AGENT_BROWSER_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/28.0.1500.52 Safari/537.36",
)
DEBUG_LOG = deque(maxlen=200)
DEBUG_LOG_COUNTER = 0


def install(ctx):
    # Check for agent-browser binary
    if not shutil.which("agent-browser"):
        ctx.log("agent-browser not found. See https://agent-browser.dev/installation to use the browser extension.")
        ctx.disabled = True
        return

    def ensure_dir(path):
        os.makedirs(path, exist_ok=True)
        return path

    def get_browser_dir(req=None):
        user = ctx.get_username(req) if req else None
        return ensure_dir(os.path.join(ctx.get_user_path(user=user), "browser"))

    def get_script_dir(req):
        return ensure_dir(os.path.join(ctx.get_user_path(user=ctx.get_username(req)), "browser", "scripts"))

    def get_profile_dir(req):
        return ensure_dir(os.path.join(ctx.get_user_path(user=ctx.get_username(req)), "browser", "profile"))

    def get_state_file(req):
        return os.path.join(ctx.get_user_path(user=ctx.get_username(req)), "browser", "state.json")

    def _add_debug_log(cmd_str, result, duration):
        global DEBUG_LOG_COUNTER
        DEBUG_LOG_COUNTER += 1
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", result.get("error", ""))
        rc = result.get("returncode", -1)
        DEBUG_LOG.append(
            {
                "id": DEBUG_LOG_COUNTER,
                "ts": time.time(),
                "cmd": cmd_str,
                "rc": rc,
                "stdout": stdout,
                "stderr": stderr,
                "ok": result.get("success", False),
                "ms": round(duration * 1000),
            }
        )
        ctx.dbg(f"{cmd_str}\n{stdout}")
        if stderr.strip() if stderr else False:
            ctx.dbg(f"{rc}: {stderr}")

    async def run_browser_cmd_async(*args, timeout=30, env=None, record=True):
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
        if record:
            _add_debug_log(cmd_str, ret, time.monotonic() - t0)
        return ret

    # =========================================================================
    # Status & Screenshot Endpoints
    # =========================================================================

    async def get_status_object():
        result = await run_browser_cmd_async("eval", "({title:document.title,url:location.href})", record=False)
        running = result["success"]
        if not running:
            return None

        status = json.loads(result["stdout"]) if result["success"] and result["stdout"].strip() else {}
        url = status.get("url", "")
        if not url or url == "about:blank":
            return None
        status["running"] = True
        return status

    async def get_status(req):
        """Get current browser status including URL and title."""
        result = await get_status_object()
        if not result:
            return web.json_response({"running": False, "url": None, "title": None})
        return web.json_response(result)

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
        browser_dir = os.path.join(ctx.get_user_path(user=ctx.get_username(req)), "browser")
        screenshot_path = os.path.join(browser_dir, "screenshot.png")
        snapshot_path = os.path.join(browser_dir, "snapshot.json")

        screenshot_result, snapshot_result = await asyncio.gather(
            run_browser_cmd_async("screenshot", screenshot_path, record=False),
            run_browser_cmd_async("snapshot", "-i", "--json", snapshot_path, record=False),
        )

        success = snapshot_result["success"]
        if success and snapshot_result.get("stdout", "").strip():
            # write output to snapshot_path for next time
            try:
                snapshot = json.loads(snapshot_result["stdout"])
                status = await get_status_object()
                if status:
                    snapshot.update(status)
                Path(snapshot_path).write_text(json.dumps(snapshot))
            except Exception as e:
                ctx.err("Failed to parse snapshot JSON\n" + snapshot_result["stdout"], e)
                success = False

        if success and os.path.exists(screenshot_path):
            return web.FileResponse(screenshot_path, headers={"Content-Type": "image/png", "Cache-Control": "no-cache"})

        return web.FileResponse(
            os.path.join(os.path.dirname(__file__), "ui", "connecting.svg"),
            headers={"Content-Type": "image/svg+xml", "Cache-Control": "no-cache"},
        )

    ctx.add_get("/browser/screenshot", get_screenshot)

    async def get_snapshot(req):
        """Get interactive elements snapshot with refs."""
        browser_dir = os.path.join(ctx.get_user_path(user=ctx.get_username(req)), "browser")
        snapshot_path = os.path.join(browser_dir, "snapshot.json")
        force = req.query.get("force", "false") == "true"
        include_cursor = req.query.get("cursor", "false") == "true"
        args = ["snapshot", "-i", "--json"]
        if include_cursor:
            args.append("-C")

        parsed = None
        if force or not os.path.exists(snapshot_path):
            result = await run_browser_cmd_async(*args)
            if not result["success"]:
                raise Exception(result.get("stderr", "Snapshot failed"))
            # write output to snapshot_path for next time
            if result.get("stdout", "").strip():
                try:
                    parsed = json.loads(result["stdout"])
                    parsed.update(await get_status_object())
                    Path(snapshot_path).write_text(json.dumps(parsed))
                except Exception as e:
                    ctx.err("Failed to parse snapshot JSON\n" + result["stdout"], e)

        try:
            if not parsed:
                snapshot_json = Path(snapshot_path).read_text()
                parsed = json.loads(snapshot_json) if snapshot_json.strip() else {}
        except json.JSONDecodeError:
            parsed = {}

        return web.json_response(parsed)

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

        _browser_env = {
            "AGENT_BROWSER_PROFILE": get_profile_dir(req),
            "AGENT_BROWSER_USER_AGENT": AGENT_BROWSER_USER_AGENT,
        }
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
        await run_browser_cmd_async("state", "save", get_state_file(req))
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
        state_file = get_state_file(req)
        result = await run_browser_cmd_async("state", "save", state_file)
        return web.json_response({"success": result["success"], "path": state_file if result["success"] else None})

    ctx.add_post("/browser/state/save", save_state)

    async def load_state(req):
        """Load browser session state."""
        state_file = get_state_file(req)
        if not os.path.exists(state_file):
            return web.json_response({"error": "No saved state found"}, status=404)

        result = await run_browser_cmd_async("state", "load", state_file)
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
        scripts_dir = get_script_dir(req)
        if os.path.exists(scripts_dir):
            for name in os.listdir(scripts_dir):
                if name.endswith(".sh"):
                    path = os.path.join(scripts_dir, name)
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
        path = os.path.join(get_script_dir(req), name)

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
        path = os.path.join(get_script_dir(req), name)

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
        path = os.path.join(get_script_dir(req), os.path.basename(name))

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
        path = os.path.join(get_script_dir(req), os.path.basename(name))

        # Check for inline content (e.g. selected text)
        body = await req.json() if req.content_length else {}
        inline_content = body.get("content") if body else None

        if not inline_content and not os.path.exists(path):
            return web.json_response({"error": "Script not found"}, status=404)

        t0 = time.monotonic()
        try:
            if inline_content:
                proc = await asyncio.create_subprocess_exec(
                    "bash",
                    "-c",
                    inline_content,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "AGENT_BROWSER_SESSION": "default"},
                )
            else:
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
            result = {
                "success": False,
                "error": "Script execution timed out",
                "returncode": -1,
                "stdout": "",
                "stderr": "Script execution timed out",
            }
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

        system_prompt = None
        candidate_paths = [
            Path(os.path.join(get_browser_dir(req), name)),
            Path(os.path.join(get_browser_dir(), name)),
            Path(__file__).parent / "ui" / "generate-script.txt",
        ]

        for path in candidate_paths:
            if path.exists():
                system_prompt = path.read_text()
                break

        if not system_prompt:
            raise Exception("generate-script.txt system prompt template not found.")

        if existing_script.strip():
            user_message = f"Here is an existing browser automation script:\n\n```bash\n{existing_script}\n```\n\nModify this script to: {prompt}\n\nOutput the complete updated script."
        else:
            user_message = f"Create a browser automation script that: {prompt}"

        BROWSER_MODEL = os.getenv("BROWSER_MODEL", ctx.config.get("defaults", {}).get("text", {}).get("model"))
        if not BROWSER_MODEL:
            raise Exception(
                "No model specified for browser script generation. Set BROWSER_MODEL environment variable or configure a default text model in llms.json."
            )
        chat_request = {
            "model": BROWSER_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        try:
            response = await ctx.chat_completion(chat_request, context={"tools": "none", "nohistory":True })
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
