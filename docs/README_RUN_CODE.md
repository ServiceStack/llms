# Code Execution Tools

LLMS includes a suite of tools for executing code in various languages within a sandboxed environment. These tools are designed to allow the agent to run scripts, perform calculations, and verify logic safely.

## Supported Languages

The following tools are available:

- `run_python(code)`: Executes Python code.
- `run_javascript(code)`: Executes JavaScript code (uses `bun` or `node`).
- `run_typescript(code)`: Executes TypeScript code (uses `bun` or `node`).
- `run_csharp(code)`: Executes C# code (uses `dotnet run` with .NET 10+ single-file support).

## Sandbox Environment

Code execution happens in a temporary, restricted environment to ensure safety and stability.

### Resource Limits
To prevent infinite loops or excessive resource consumption, the following limits are enforced using `ulimit` (on Linux/macOS):

- **Execution Time**: The process is limited to **5 seconds** of CPU time.
- **Memory**:
    - Python: **1 GB** virtual memory limit.
    - JavaScript/TypeScript/C#: **8 GB** virtual memory limit (higher limit to accommodate runtime overhead).

### File System
- Each execution runs in a **clean, temporary directory**.
- Converting `LLMS_RUN_AS` will try to `chmod 777` this directory so the target user can write to it.
- **Note**: Files created during execution are transient and lost after the tool completes, unless their content is printed to stdout.

### Environment Variables
- The environment is cleared to prevent leakage of sensitive tokens or API keys.
- Only the `PATH` variable is preserved to ensure standard tools function correctly.
- For C# (`dotnet`), `HOME` and `DOTNET_CLI_HOME` are set to the temporary directory to allow write access for the runtime intermediate files.

## Running with Lower Privileges (`LLMS_RUN_AS`)

By default, code runs as the user running the LLMS process. For enhanced security, you can configure the tools to execute code as a restricted user (e.g., `nobody` or a dedicated restricted user).

### Configuration

Set the `LLMS_RUN_AS` environment variable to the username you want code to run as.

**Example:**
```bash
export LLMS_RUN_AS=nobody
```

### Requirements for `LLMS_RUN_AS`
1. **Sudo Access**: The user running the LLMS process must have `sudo` privileges configured to run commands as the target user **without a password**.
2. **Permissions**: The LLMS process attempts to grant read/write access to the temporary execution directory for the target user (via `chmod 777`).

**Example `sudoers` configuration:**
```bash
# Allow 'mythz' to run commands as 'nobody' without password
mythz ALL=(nobody) NOPASSWD: ALL
```

### How it Works
When `LLMS_RUN_AS` is set:
1. A temporary directory is created.
2. Permissions on the directory are relaxed so the restricted user can access it.
3. The command is executed wrapped in `sudo -u <user>`.
   - Example: `sudo -u nobody bash -c 'ulimit -t 5; ... python script.py'`

This execution model ensures that even if malicious code escapes the `ulimit` sandbox or accesses the filesystem, it is restricted by the OS-level permissions of the `nobody` (or specified) user.
