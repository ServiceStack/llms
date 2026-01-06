# Core Tools

Core System Tools providing essential file operations, memory persistence, math expression evaluation, and code execution.

These tools are available by default in the `ctx` object.

## Memory Tools

Functions for persistent key-value storage.

### `memory_read(key: str) -> Any`

Read a value from persistent memory.

- **key**: The key to retrieve.

### `memory_write(key: str, value: Any) -> bool`

Write a value to persistent memory.

- **key**: The storage key.
- **value**: The value to store.

## File System Tools

All file system operations are restricted to the current working directory for safety.

### `read_file(path: str) -> str`

Read a text file from disk.

- **path**: Relative path to the file.

### `write_file(path: str, content: str) -> bool`

Write text to a file (overwrites existing content).

- **path**: Relative path to the file.
- **content**: Text content to write.

### `list_directory(path: str) -> str`

List directory contents including file names, sizes, and modification times.

- **path**: Relative path to the directory.

### `glob_paths(pattern: str, extensions: List[str] = None, sort_by: str = "path", max_results: int = 100) -> Dict`

Find files and directories matching a glob pattern.

- **pattern**: Glob pattern to match.
- **extensions**: Optional list of file extensions to filter by.
- **sort_by**: Sort criteria ("path", "modified", "size").
- **max_results**: Maximum number of results to return.

## Math & Logic

### `calc(expression: str) -> str`

Evaluate a mathematical expression. Supports arithmetic, comparison, boolean operators, and common math functions.

- **expression**: The mathematical expression string (e.g., `sqrt(16) + 5`).

## Code Execution

Execute code in sandboxed environments. Requires the respective runtimes to be installed on the host system.

### `run_python(code: str) -> Dict`

Execute Python code.

- **code**: Python script content.

### `run_javascript(code: str) -> Dict`

Execute JavaScript code. Requires `bun` or `node`.

- **code**: JavaScript content.

### `run_typescript(code: str) -> Dict`

Execute TypeScript code. Requires `bun` or `node`.

- **code**: TypeScript content.

### `run_csharp(code: str) -> Dict`

Execute C# code. Requires `dotnet`.

- **code**: C# content.

## Utilities

### `get_current_time(tz_name: str = None) -> str`

Get the current time in ISO-8601 format.

- **tz_name**: Optional timezone name (e.g., 'America/New_York'). Defaults to UTC.