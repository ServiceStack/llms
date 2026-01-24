# Computer Use Tools

This extension provides a set of tools that allow an Agent to interact with a computer environment in a way similar to a human user. It includes capabilities for screen interaction (mouse/keyboard), shell execution, and file editing. Based on [Anthropic's computer use tools](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo).

## Available Tools

### 1. Computer Tool (`computer`)
Allows interaction with the screen, keyboard, and mouse.

**Capabilities:**
- **Mouse Interaction**: Move cursor, click (left, right, middle, double, triple), click & drag.
- **Keyboard Interaction**: Type text, press specific keys or key combinations.
- **Screen**: Take screenshots, get cursor position.
- **Zooming**: Zoom into specific regions of the screen (Action: `zoom`).

**Key Parameters:**
- `action`: The action to perform (e.g., `mouse_move`, `left_click`, `type`, `screenshot`, `zoom`).
- `coordinate`: `(x, y)` coordinates for mouse actions.
- `text`: Text to type.
- `key`: Key sequence to press (e.g., `Return`, `Control+c`).
- `region`: `(x0, y0, x1, y1)` region for zooming.

### 2. Bash Tool (`bash`)
Provides a persistent shell session to execute command-line instructions.

**Capabilities:**
- **Execute Commands**: Run any bash command.
- **Persistent Session**: State (like environment variables, working directory) is preserved between calls within the same session.
- **Process Management**: Can restart the session if needed.
- **Open Files/URLs**: Helper function `open` allows opening files or URLs using the system's default handler (`xdg-open`, `open`, or `start`).

**Key Parameters:**
- `command`: The bash command to execute.
- `restart`: Boolean to restart the session.

### 3. Edit Tool (`str_replace_editor`)
A filesystem editor for viewing and modifying files.

**Capabilities:**
- **View**: Read file contents or list directories.
- **Create**: Create new files with content.
- **String Replace**: Replace unique strings in a file (robust for LLM editing).
- **Insert**: Insert text at specific line numbers.
- **Undo**: Undo the last edit to a file.

**Key Parameters:**
- `command`: The edit command (`view`, `create`, `str_replace`, `insert`, `undo_edit`).
- `path`: Absolute path to the file or directory.
- `file_text`: Content for file creation.
- `old_str` / `new_str`: Strings for replacement.

---

## Capabilities & Workflows

These tools are designed to work together to enable complex end-to-end tasks. An Agent can act as a developer, tester, or general user.

### Example: "Build a Tetris web app in a tetris folder, open it then take a screenshot"

To achieve this high-level task, the Agent would sequence the tools as follows:

1.  **Create the Project Structure**
    *   **Tool**: `bash`
    *   **Command**: `mkdir -p tetris`
    *   *Result*: Creates the folder.

2.  **Create the Application Files**
    *   **Tool**: `edit` (command: `create`)
    *   **Path**: `/path/to/tetris/index.html`
    *   **Content**: (HTML code for Tetris game)
    *   *Result*: Writes the HTML file.

3.  **Open the Application**
    *   **Tool**: `bash` (via helper `open`) or `bash` directly.
    *   **Command**: `xdg-open /path/to/tetris/index.html` (Linux) or just `python -m http.server` and open localhost.
    *   *Result*: Opens the file in the default web browser.

4.  **Wait & Verify**
    *   **Tool**: `computer`
    *   **Action**: `wait` or `screenshot` to see if it loaded.

5.  **Take a Screenshot**
    *   **Tool**: `computer`
    *   **Action**: `screenshot`
    *   *Result*: Captures the visual state of the running Tetris app for the user to see.

### How it handles the "Build a Tetris..." request:
When a user gives the command:
> "Build a Tetris web app in a tetris folder, open it then take a screenshot"

The Agent decomposes this into:
1.  **"Build... in a tetris folder"** -> Uses `bash` to make the directory and `edit` to write the `index.html` / `style.css` / `script.js` files.
2.  **"Open it"** -> Uses `bash` to run a server or open the file in a browser.
3.  **"Take a screenshot"** -> Uses `computer` to verify the visual output.

This combination allows the Agent to not just generate code, but **verify** it visually and interactively, closing the loop on development tasks.
