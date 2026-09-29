# Projects Extension

The **Projects** extension provides a workspace management system for `llms.py`. It allows users to create dedicated project folders under `$LLMS_HOME/user/<user>/projects/<folder>` (or `$LLMS_HOME/user/default/projects/<folder>` for anonymous users) that AI Agents are permitted to read from and write to.

## Key Concepts

- **Projects own chats**: every chat belongs to a project, or to none (listed under **Recents**). The chat
  sidebar groups chats into project folders.
- **Workspace Sandboxing**: an agent run can only read and write its chat's project folder. The folder is
  captured when a message is sent, so moving a chat later only affects future messages.
- **Stable identity**: each project has a generated `id`; renaming a project or its folder keeps its chats.
- **Auto Kebab-Case Folders**: Folder names are automatically generated in kebab-case from the project name, but can be manually overridden.
- **Relative Publish Paths**: The `publish` output directory is specified as a relative path combined with the project folder path (e.g. `dist`).
- **Automatic Folder Creation**: Project directories are automatically created on disk upon saving if they do not exist.

---

## Using Projects in the UI

1. **Choosing a chat's project**: click the project chip at the top of the chat prompt (it shows the
   project name, or **No project**). Search and pick a project, choose **Don't work in a project**, or
   create a **New project**, which applies to the chat you're writing.
2. **Starting a chat in a project**: hover a project folder in the sidebar and click its new-chat button,
   or select the project in the project manager. Both open an unsent draft in that project.
3. **Project manager**: open it from the **Projects** heading's `…` button in the sidebar (`+` creates a
   project), or a folder's menu → **Edit project…**.
   - **Project Name & Folder**: the folder defaults to a kebab-case slug of the name (e.g. `tic-tac-toe`).
   - **Show folder in sidebar**: hide a folder you don't use; a folder's menu can also hide it, and
     selecting the project again shows it.
   - **Publish Build Directory**: optional relative path (e.g. `dist`).
4. **Deleting Projects**: select it in the project manager and click **Delete Project**. Its chats move to
   Recents; deleting is refused while one of its chats has a running agent.

---

## Technical Configuration Details

Projects are persisted locally in a JSON file format under the user's data directory:
- **Anonymous Path**: `$LLMS_HOME/user/default/projects/projects.json`.
- **User-Specific Path** (when authenticated): `$LLMS_HOME/user/{username}/projects/projects.json`.

### Schema Example (`projects.json`):
```json
[
  {
    "id": "3ea0eb83-8d61-419b-a5f7-854915456f61",
    "name": "Tic Tac Toe",
    "folder": "tic-tac-toe",
    "description": "Creating a Tic Tac Toe game in React",
    "publish": "dist",
    "publishedUrl": "https://ai.llmspy.org/p/user/Tic_Tac_Toe",
    "showInSidebar": true
  }
]
```

The `id` is assigned automatically the first time a project is read or saved; keep it when editing the
file by hand, since chats reference projects by `id`. See [`docs/CHAT_THREADS.md`](../../../docs/CHAT_THREADS.md)
for how chats, projects and agent workspaces fit together.
