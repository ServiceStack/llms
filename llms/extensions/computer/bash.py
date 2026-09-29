import asyncio
import locale
import os
import sys
from typing import Annotated, Any, Literal

from .base import BaseTool, CLIResult, ToolError, ToolResult


class _BashSession:
    """A persistent native shell session (Bash on Unix, cmd.exe on Windows)."""

    _started: bool
    _process: asyncio.subprocess.Process

    _output_delay: float = 0.2  # seconds
    _timeout: float = 120.0  # seconds
    _sentinel: str = "__LLMS_COMMAND_COMPLETE__"

    def __init__(self):
        self._started = False
        self._timed_out = False
        self._is_windows = sys.platform == "win32"
        self.command = (
            os.environ.get("COMSPEC", "cmd.exe")
            if self._is_windows
            else os.environ.get("LLMS_SHELL", "/bin/bash")
        )
        self._encoding = locale.getpreferredencoding(False) if self._is_windows else "utf-8"

    async def start(self):
        if self._started:
            return

        args = [self.command, "/D", "/Q"] if self._is_windows else [self.command]
        kwargs = {} if self._is_windows else {"start_new_session": True}
        from llms.execution_context import workspace_scope
        scope = workspace_scope.get()
        directories = scope.get("directories", []) if scope else []
        if directories:
            kwargs["cwd"] = directories[0]
        self._process = await asyncio.create_subprocess_exec(
            *args,
            bufsize=0,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )

        self._started = True

    def stop(self):
        """Terminate the bash shell."""
        if not self._started:
            raise ToolError("Session has not started.")
        if self._process.returncode is not None:
            return
        self._process.terminate()

    async def run(self, command: str):
        """Execute a command in the bash shell."""
        if not self._started:
            raise ToolError("Session has not started.")
        if self._process.returncode is not None:
            return ToolResult(
                system="tool must be restarted",
                error=f"bash has exited with returncode {self._process.returncode}",
            )
        if self._timed_out:
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            )

        # we know these are not None because we created the process with PIPEs
        assert self._process.stdin
        assert self._process.stdout
        assert self._process.stderr

        # send command to the process
        if self._is_windows:
            command_line = f"{command} & echo {self._sentinel}\r\n"
        else:
            command_line = f"{command}; printf '%s\\n' '{self._sentinel}'\n"
        self._process.stdin.write(command_line.encode(self._encoding, errors="replace"))
        await self._process.stdin.drain()

        # read output from the process, until the sentinel is found
        try:
            async with asyncio.timeout(self._timeout):
                while True:
                    await asyncio.sleep(self._output_delay)
                    # if we read directly from stdout/stderr, it will wait forever for
                    # EOF. use the StreamReader buffer directly instead.
                    output = self._process.stdout._buffer.decode(  # pyright: ignore[reportAttributeAccessIssue]
                        self._encoding, errors="replace"
                    )
                    if self._sentinel in output:
                        # strip the sentinel and break
                        output = output[: output.index(self._sentinel)]
                        break
        except asyncio.TimeoutError:
            self._timed_out = True
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            ) from None

        if output.endswith("\r\n"):
            output = output[:-2]
        elif output.endswith("\n"):
            output = output[:-1]

        error = self._process.stderr._buffer.decode(  # pyright: ignore[reportAttributeAccessIssue]
            self._encoding, errors="replace"
        )
        if error.endswith("\r\n"):
            error = error[:-2]
        elif error.endswith("\n"):
            error = error[:-1]

        # clear the buffers so that the next output can be read correctly
        self._process.stdout._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]
        self._process.stderr._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]

        return CLIResult(output=output, error=error)


class BashTool20250124(BaseTool):
    """
    A tool that allows the agent to run shell commands.
    The tool parameters are defined by Anthropic and are not editable.
    """

    _session: _BashSession | None

    api_type: Literal["bash_20250124"] = "bash_20250124"
    name: Literal["bash"] = "bash"

    def __init__(self):
        self._session = None
        super().__init__()

    def to_params(self) -> Any:
        return {
            "type": self.api_type,
            "name": self.name,
        }

    async def __call__(self, command: str | None = None, restart: bool = False, **kwargs):
        if restart:
            if self._session:
                self._session.stop()
            self._session = _BashSession()
            await self._session.start()

            return ToolResult(system="tool has been restarted.")

        if self._session is None:
            self._session = _BashSession()
            await self._session.start()

        if command is not None:
            return await self._session.run(command)

        raise ToolError("no command provided.")


class BashTool20241022(BashTool20250124):
    api_type: Literal["bash_20250124"] = "bash_20250124"  # pyright: ignore[reportIncompatibleVariableOverride]


g_tool = None
run_tools = {}


async def release_run_shell(run_id):
    tool = run_tools.pop(run_id, None)
    if tool and tool._session and tool._session._started:
        tool._session.stop()
        await tool._session._process.wait()


async def run_bash(
    command: Annotated[str | None, "Command to run"],
    restart: Annotated[bool, "Restart the bash session"] = False,
) -> list[dict[str, Any]]:
    """
    Run a command in a persistent native shell session.
    """
    global g_tool
    from llms.execution_context import workspace_scope
    scope = workspace_scope.get()
    if scope is not None and scope.get("runId") is not None:
        tool = run_tools.setdefault(scope["runId"], BashTool20241022())
    else:
        if g_tool is None:
            g_tool = BashTool20241022()
        tool = g_tool
    result = await tool(command=command, restart=restart)
    if isinstance(result, Exception):
        raise result
    else:
        return result.to_tool_results()


async def open(target: Annotated[str, "URL or file path to open"]) -> list[dict[str, Any]]:
    """
    Open a URL or file using the appropriate system opener, uses `xdg-open` on Linux, `open` on macOS, and `start` on Windows.
    """
    target = target.strip()
    if not target:
        raise ValueError("No target specified")

    if sys.platform == "win32":
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise RuntimeError("Windows shell opener is unavailable")
        startfile(target)
        return ToolResult(system=f"Opened {target}").to_tool_results()

    cmd = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    result = CLIResult(
        output=stdout.decode(errors="replace").strip() or None,
        error=stderr.decode(errors="replace").strip() or None,
        system=f"Opened {target}" if process.returncode == 0 else None,
    )
    return result.to_tool_results()
