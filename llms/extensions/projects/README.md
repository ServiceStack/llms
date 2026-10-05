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

## Creating Git-backed projects

When the Git extension is enabled and `git` is installed, **New project** offers two sources:

- **New project** creates an empty workspace. **Initialize Git repository** starts checked; uncheck it
  for a plain folder. Git repositories start on `main`, without generated files or an automatic commit.
- **Clone repository** accepts an HTTPS or SSH repository URL. The name and folder derive from the
  repository name and remain editable. **More options** includes an optional branch; otherwise the
  remote's default branch is used. Cloning preserves history and the source remote.

The resolved destination is shown beneath the folder field. New folders must be directly inside the
user's managed projects directory; existing folders are never overwritten. Repository scripts,
submodule checkout and dependency installation do not run automatically. Git LFS content is not fetched
automatically; users can fetch it separately using their normal Git tooling.

Creation shows progress and offers cancellation. Closing the dialog leaves the operation running;
reopening New project restores active progress. Failed or interrupted creation offers Retry and Edit
details. A completed clone is registered before it becomes an agent workspace. Completion applies to
the originating chat only while that chat remains selected; otherwise **Open project** is explicit.

In local mode (`LLMS_MODE=local`, the default), cloning can use existing machine Git credential helpers
and SSH identities. SSH requires the host to already be trusted in `known_hosts`. No credentials should
be embedded in the URL. In hosted mode, the initial implementation supports public HTTPS repositories
on `github.com`, `gitlab.com` and `bitbucket.org`. Administrators can change that list using
`"git_clone_hosts": ["github.com", "git.example.org"]` in configuration. Hosted cloning does not borrow
the operator's global Git credentials or SSH keys. GitHub sign-in alone does not grant repository access.

Creation state is stored in `projects/.creation.sqlite`; incomplete clones use hidden `.create-*`
folders. Interrupted attempts with uncertain child-process ownership are retained for safe recovery
instead of being deleted automatically. The Git sidebar supports staging, unstaging and local commits
with reviewed staged diffs and an explicit commit message, for both project and home repositories.
Synchronization, external workspace registration and GitHub publishing remain planned in
[GIT_PROJECTS.md](../../../GIT_PROJECTS.md).

| API | Purpose |
|---|---|
| `GET /ext/projects/creation/options` | Creation/Git capabilities, destination root and recent operations |
| `POST /ext/projects/create` | Idempotent new-folder or clone creation, returning an operation |
| `GET /ext/projects/creation/operations/{id}` | Owned operation snapshot; `revision` enables bounded long-poll waiting |
| `POST /ext/projects/creation/operations/{id}/cancel` | Cancel pending/running creation |
| `POST /ext/projects/creation/operations/{id}/retry` | Retry a failed/interrupted/cancelled attempt |

Metadata edits continue to use the existing save routes and never initialize or clone a repository.
Cloned projects show their saved **Repository URL** in the project manager, with copy and open actions.
SSH sources on GitHub, GitLab and Bitbucket open the corresponding HTTPS repository page; copying
always keeps the exact clone URL. This is the original source, rather than a live lookup of `origin`.

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
4. **Reordering**: click **Reorder** above the active list, then drag a folder row up or down. Touch and
   arrow keys on a focused handle work too. Order saves immediately and is shared with the chat sidebar
   and project pickers; **Done** hides the handles.
5. **Archiving**: select a project and click **Archive project**, or use a sidebar folder's menu.
   Archiving automatically hides the folder from the main thread sidebar and removes it from the
   active list and project pickers. Files, conversations, drafts and existing run workspaces remain
   attached to its stable ID. **Archived Projects** at the bottom of the manager sidebar opens a page
   searchable by name, folder and description. **Unarchive** appends it to the active list and restores
   its previous sidebar visibility (a previously hidden folder stays hidden).
6. **Deleting Projects**: select it in the project manager and click the **Delete project** trash icon. Its chats move to
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
    "staticPublication": {
      "publishedPath": "/srv/www/p/user/tic-tac-toe",
      "urlPath": "/p/user/tic-tac-toe/",
      "publishedUrl": "https://example.com/p/user/tic-tac-toe/",
      "publishedAt": "2026-10-05T00:00:00+00:00"
    },
    "showInSidebar": true
  }
]
```

The `id` is assigned automatically the first time a project is read or saved; keep it when editing the
file by hand, since chats reference projects by `id`. See [`docs/CHAT_THREADS.md`](../../../docs/CHAT_THREADS.md)
for how chats, projects and agent workspaces fit together.

Array order is the project's display order. Archived records have `archived: true`,
`showInSidebar: false`, and `archivedSidebarVisibility` recording the visibility to restore.
`GET /ext/projects/projects.json` still returns all records so clients never mistake an archive for
deletion. `POST /ext/projects/order` takes `{"ids": ["active-id", "…"]}` with every active ID once;
stale membership returns 409. `PATCH /ext/projects/archive/{id}` takes `{"archived": true|false}`.
Archive state is preserved through legacy metadata/bulk saves, including bulk saves that omit archives.

Project output can publish through the independently enabled `share_static` and `share_llmspy`
extensions. See [static sharing configuration](../share_static/README.md).
`staticPublication` is server-owned and preserved across project edits; `publishedUrl` remains the
remote publication link.
