# Projects Extension

The **Projects** extension provides a workspace management system for `llms.py`. It allows users to create dedicated project folders under `$LLMS_HOME/user/<user>/projects/<folder>` (or `$LLMS_HOME/user/default/projects/<folder>` for anonymous users) that AI Agents are permitted to read from and write to.

## Key Concepts

- **Workspace Sandboxing**: Restricts filesystem access exclusively to the active project folder.
- **Auto Kebab-Case Folders**: Folder names are automatically generated in kebab-case from the project name, but can be manually overridden.
- **Relative Publish Paths**: The `publish` output directory is specified as a relative path combined with the project folder path (e.g. `dist`).
- **Automatic Folder Creation**: Project directories are automatically created on disk upon saving if they do not exist.

---

## Managing Projects in the UI

1. **Accessing the Project Manager**:
   - Click the **Workspaces & Projects** dropdown (top-left of the application header, displaying the active project name or *Default Workspace*).
   - Select **Manage Projects** to open the project manager interface.

2. **Creating & Editing Projects**:
   - **New Project**: Click **New Project** to start.
   - **Project Name & Folder**: Provide a unique name; the folder name will automatically default to a kebab-case slug of the project name (e.g. `tic-tac-toe`).
   - **Publish Build Directory**: Optionally provide a relative path (e.g. `dist`) relative to the project directory.
   - Click **Save** to apply. The project folder will be automatically created on the server if needed.

3. **Deleting Projects**:
   - To delete a project, select it in the project manager list and click **Delete Project**. Deleting the active project automatically resets the active workspace back to Default.

---

## Selecting an Active Project

Use the project selector dropdown in the header to switch between defined projects:
- **Default Workspace**: No active project restriction.
- **Custom Projects**: Restricts AI agent filesystem interactions to `$LLMS_HOME/user/<user>/projects/<folder>`.

---

## Technical Configuration Details

Projects are persisted locally in a JSON file format under the user's data directory:
- **Anonymous Path**: `$LLMS_HOME/user/default/projects/projects.json`.
- **User-Specific Path** (when authenticated): `$LLMS_HOME/user/{username}/projects/projects.json`.

### Schema Example (`projects.json`):
```json
[
  {
    "name": "Tic Tac Toe",
    "folder": "tic-tac-toe",
    "description": "Creating a Tic Tac Toe game in React",
    "publish": "dist",
    "publishedUrl": "https://ai.llmspy.org/p/user/Tic_Tac_Toe"
  }
]
```
