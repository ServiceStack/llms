# Workspace and Skills browsing

The top-right workspace button opens a read-only directory browser. SVG buttons switch between Files
and extension panels such as Git. Both toolbars use the same 36px height and 28px icon buttons. The file tree loads a folder listing only when opened, caches it until refresh or a
project change, and keeps parents and siblings visible. Each folder has a single open/closed folder icon; clicking the row toggles expansion. Refresh preserves expanded folders.
The root breadcrumb below the toolbar highlights as a button on hover. Click it to open the allowed-directory dropdown; choosing one updates the URL. Arrow keys, Home/End, and Escape work in the dropdown. The picker is available in every sidebar view. Breadcrumbs and URL restoration expand only the
selected ancestor chain. Selecting a file shows its contents
in the main panel with a path and filename title bar. Click a directory breadcrumb to return to that
directory in the right sidebar. The selected chat or draft supplies the project; without one, the browser
uses a separate snapshot of the user's initial allowed directories, starting at the first. For the
local default user this includes the directory where llms was started, the temporary directory, and
`.agent`. It never inherits the legacy active project. Verified Admin accounts can use the startup directories when their own initial permissions are empty.
Other users keep their own initial permissions; a user without any browses their own private
`~/.llms/user/<user>/workspace` (there is no shared central workspace). This does not change a durable run's workspace.

Selections use Vue Router query parameters. Navigation preserves unrelated query parameters.

- Workspace: `workspace=1`, `workspacePath`, `workspaceFile`, `workspaceView` (`files` or a registered extension ID, such as `git`; legacy `history` links resolve to `git` while that extension is enabled),
  `workspaceProject` (the owning project ID), `workspacePreview` (optional registered main-preview ID). Closing sets `workspace=0` and clears the selection keys. Page navigation preserves an open sidebar
  and clears the file preview so the destination page is visible. Routes can close it explicitly with
  `meta.workspaceSidebar = false`. Changing the chat's project resets
  directory and file selections with `replace`, preventing old paths from being read under the new project's policy.
  The selected sidebar view stays open across project and no-project threads, including extension views.
  Clicking the already selected thread also preserves the open sidebar and selected view.
- Skills (`/skills`): `skill` (skill name), `file` (relative filename), `dir` (relative directory).
  Selecting a directory clears `file`; clicking a breadcrumb selects and highlights the directory in the
  skill tree. Refresh and Back/Forward restore selections. Route changes prompt before discarding edits.

`GET /ext/projects/explorer` resolves the authenticated user's explicit project or initial explorer directories server-side. Paths and symlink targets must stay inside that workspace. Text and SVG previews are limited
to 1 MiB; raster images to 10 MiB. Other binary and larger files show an explanatory message. Directory lists are capped at 2,000 entries.
Files never invoke Git. The optional `git` extension owns `GET /ext/git/workspace` and its UI panel.
It discovers repositories only inside the allowed root and returns working-tree changes and up to 50 commits.
It disables itself before registering routes or UI when the Git executable is missing. To disable it explicitly,
add `"git"` to `disable_extensions` in `llms.json` and restart. The icon disappears, and saved Git URLs fall back to Files.
Directories without a repository show an empty state in the Git panel.
Selecting Git loads fresh status/history, including when clicking the already selected Git tab. While
the Git view is open, the current thread's transition to completion (including failure or cancellation)
refreshes that workspace's Git data and expanded commit files. Repeated terminal snapshots, streaming
updates, and completions belonging to a different project do not refresh it. The watcher uses the existing
reactive thread state updated by SSE/long-poll; there is no extra polling, and Files or a closed workspace
issue no Git requests when a thread finishes.

The Git panel separates **Staged changes** and **Working changes**. Use a file's plus/minus button to
stage/unstage it, or the section action to stage/unstage all listed files. Both sections can collapse.
Enter a message and choose **Commit** to commit only the current index. Initial commits, deletions,
renames, and files appearing in both sections are supported; later working-tree edits stay unstaged.
Commit messages are kept in browser memory per project/repository, including a separate home draft.
Successful operations refresh status/history immediately; errors retain the message and staged state.
Right-click an unstaged file (or press Shift+F10 while it is focused) for **Discard Changes**. Confirming
restores a tracked file from the index, keeping staged content; an untracked file is deleted individually.
Discard checks the reviewed index and file metadata, rejects stale selections, and shares the repository
and agent-submission locks. It does not recurse into folders, submodules, or linked paths. Content-filtered
files require a Git client. A discarded open diff closes or switches to its remaining staged comparison.

The ellipsis to the right of the branch opens the repository menu: **Pull**, **Push**, and **Commit**,
**Changes**, and **Stash** submenus. Submenus open on hover or click; arrow keys navigate, Right enters,
Left returns, and Escape restores focus. On narrow screens a submenu overlays its parent with a Back
button. Disabled actions explain their requirements in tooltips and busy operations block other writes.
**Commit** uses the staged index, or confirms staging all files when nothing is staged. **Commit Staged**
uses only the index; **Commit All** stages tracked changes and untracked files before committing.
All three use the typed message and configured/entered author. **Undo Last Commit** moves the branch
back while retaining the index and working files, including undoing the initial commit; an empty message
editor receives the undone message. Undo is disabled for detached HEAD or a commit already present on
a known remote-tracking branch.
**Stage All Changes**, **Unstage All Changes**, and confirmed **Discard All Changes** operate on the
reviewed repository. Discard affects only unstaged content, retains staging, and deletes individual
untracked files after preflighting the entire selection. **Stash** saves tracked changes, **Stash
(Include Untracked)** also saves new files, and **Stash Staged** saves only the index (Git 2.35+).
Stashing requires a first commit and a Git author. Apply/Pop require no tracked or staged edits and use
the reviewed latest stash ID. Apply retains the stash; Pop removes it only after a successful apply.
Conflicts retain the stash and require resolution with a Git client. Filtered/linked paths and submodules
require a Git client for these bulk operations.
The commit editor starts with one line, grows with wrapping/newlines up to 240px, and uses the chat
prompt's gray border in each theme. The sparkle button generates an editable commit subject from all
staged diffs. It shows progress, keeps text on failure, and preserves any manual edits made while the
request is running; switching projects discards late suggestions for the previous draft.

`POST /ext/git/repositories/message` validates the authenticated workspace and reviewed `indexRevision`,
concatenates the complete staged patch (including binary notices), and applies `defaults.commit` in
`llms.json`. The template configures the model and instructions; `{diffs}` in its final user message is
replaced with the combined changes, or the diff is appended when no placeholder is provided. Missing
keys in older configs use the packaged template; an explicit `null` disables generation. The default
asks for a short, imperative, high-level subject instead of a file inventory. Model requests use no
tools, agent filters, or chat persistence. Input is bounded at 1 MiB and checked against the model's
context; oversized input is rejected rather than partially summarized. Index changes during reading
or generation invalidate the suggestion. Generation never stages files or makes a commit.
Configured Git author details appear below Commit. When absent, enter name/email for this commit;
**Remember for this repository** explicitly saves them to local Git config. Hosted mode ignores the
server operator's global/system identity. No identity is inferred from the llms account.

`POST /ext/git/repositories/{stage|unstage|commit|discard}` resolves the authenticated project's or home's
allowed directories again. Stage/unstage receive literal relative `paths`. Commit receives `message`,
`identity`, `saveIdentity`, `indexRevision`, and `requestId`; the revision rejects stale reviewed indexes,
and a durable receipt makes retrying safe after a lost response. The repository and normal Git index
locks prevent overlapping writes, while a shared submission gate prevents a new agent run racing with
a Git write. Active queued/running/approval-waiting runs in the project or overlapping captured workspace
block mutations. Read-only views remain available. Git hooks and signing are disabled in this flow;
files using content filters and repositories with linked/external metadata need a Git client. These
operations do not push to a remote.
The same mutation route supports `commit-all`, `stage-all`, `unstage-all`, `discard-all`, `undo`, `stash`,
`stash-untracked`, `stash-staged`, `stash-apply`, and `stash-pop`. Repository menu requests include the
reviewed `head`, `branch`, `indexRevision`, and `workspaceRevision`; stash Apply/Pop additionally check
`stashId`. The workspace token hashes status and file metadata, and Git status reads disable optional
index writes. Commit All stages using an isolated index under the normal index lock and shares Commit's
durable receipts. Stash operations use Git's normal index/ref locks and the shared repository/submission
gate. Every success or failure refreshes status so partial/conflicting Git results remain reviewable.
Discard receives one literal relative path plus `indexRevision` and `worktreeRevision`, and changes only
that worktree file. The revision token uses size, mode, timestamps and filesystem identity without reading
every working file's content during status refresh.

The repository header has **Pull** and **Push** icons with compact labels. They stay visible when
disabled, with tooltips explaining missing remotes, detached/unborn branches, working changes, or
hosted push restrictions. Multiple remotes show a selector; the branch's configured upstream is used
when present, otherwise the current branch. Ahead/behind counts use local remote-tracking refs and update
after sync. Pull fetches the selected branch and fast-forwards only; local changes must be committed or
stashed first. Push sends the reviewed commit without force or implicit tags. The first sync records an
upstream without replacing an existing one. Loading feedback disables competing changes, errors remain
visible, and completion refreshes status/history without reloading the page. Late responses remain with
their origin project. `POST /ext/git/repositories/{push|pull}` authenticates and resolves the explicit
workspace, checks the reviewed branch/HEAD, and shares the repository and agent-submission locks.
Local mode uses the user's Git credential helper/SSH identity non-interactively; hosted mode supports
public HTTPS pulls from `git_clone_hosts` and keeps push disabled until per-user authorization exists.
Transport is bounded to 120 seconds and errors never expose raw Git output or credentials. Divergence,
checkout filters and linked metadata require a Git client. These are bounded requests rather than
durable background jobs; refresh Git to inspect the outcome after a disconnected request before retrying.
When the tree is clean and the selected remote has pending commits or an unpublished branch, Commit
becomes **Sync Changes** with incoming/outgoing counts. `POST /ext/git/repositories/sync` fetches and
fast-forwards before pushing under one lock. It preserves local commits on divergence and refreshes
after failures. Combined sync requires local push access; explicit Pull and Push stay available.

Clicking a staged file compares **HEAD → index** (`GET /ext/git/diff?staged=1` and
`workspacePreview=git-staged`). The hidden preview registration shares the Git diff component without
adding a sidebar tab. Staging/unstaging an open diff switches its comparison when appropriate, and
committing closes a preview whose changes have been committed.

Clicking a working change opens an inline unified diff in the main panel and keeps the Git sidebar open.
Removed lines are red, added lines are green, and context lines retain their normal background. Index and
working-tree line numbers, hunk headers, and added/removed counts make changes easy to inspect. The comparison
is **index → working tree**, so previously staged changes are excluded. Deleted files remain clickable and show
removed lines, even when their parent directory has gone. Untracked files compare against an empty file.
`GET /ext/git/diff?file=…&path=…&projectId=…` resolves the same authenticated workspace policy as the explorer;
paths and symlink targets remain within the root. File/index blobs are limited to 1 MiB, and diff previews to
1 MiB or 10,000 lines. Binary files, merge conflicts, and oversized diffs show an explanation. Git external
diff/text-conversion drivers are disabled. Refresh reloads the diff; Close returns to the destination page.
Diff links use `workspaceFile` plus `workspacePreview=git` and support Back/Forward.

Recent commits use one 28px row each: a subject, graph dot/line, and an optional inline ref badge.
Author, timestamp, full hash, and message details stay in the hover/focus popover. Right-click a commit
or press Shift+F10 while it is focused to open a context menu with
**Open on GitHub**, **Copy Commit Hash**, and **Copy Commit Message**. The GitHub action uses the
selected remote's sanitized GitHub URL and opens the exact commit in a new tab; other hosts disable it.
Copy uses the full object ID or complete stored commit message, including its body. Arrow keys navigate,
Escape returns focus to the row, and outside clicks, scrolling, refresh and project changes dismiss menus.
Clicking a commit
expands its changed files; Left/Right also collapse/expand it. File lists load on demand from
`GET /ext/git/commit?commit=…&path=…&projectId=…`, remain cached until refresh, and show inline loading,
retry, and empty states. Refresh preserves expanded commits. Clicking a changed file opens a historical
diff through `GET /ext/git/diff` with an additional `commit` hash. The comparison is first parent → commit,
including for merges; an initial commit compares against an empty tree. Added, deleted, and renamed files
work even if the working tree has since changed. Historical diffs apply the same workspace and preview
limits as unstaged diffs. `workspaceCommit` records the selected revision, so Back/Forward and reloading
restore it and expand its commit. Working-tree selections, directory/view changes, closing the preview,
and project/page changes clear this revision.

## Right-sidebar extension hook

`ctx.setRightIcons()` registers tabs using the same icon definitions as the top/left bar, plus a `panel`:

```js
ctx.setRightIcons({
    notes: {
        name: 'Notes', title: 'Workspace notes', component: NotesIcon, panel: NotesPanel,
        // Optional: preview: NotesPreview,
        // Optional: aliases: ['old-notes'], isVisible: ({workspace, projectId}) => ...
    },
})
```

`files` is the built-in tab. An extension's panel receives `workspace` (validated roots and selected directory),
`projectId`, and `refreshKey` (incremented by the refresh control). It owns its loading/error/empty states and API
requests, cancels or ignores stale responses, emits `busy` during loading, and can emit `file` with `{path}`
to open the shared file preview. A definition may also supply `preview`, a main-panel component; emitting
`file` with `{path, preview: 'notes', directoryPath}` selects that extension's preview. An optional `commit`
sets `workspaceCommit` for Git's historical diff preview. `directoryPath` can
preserve the existing browser directory when the selected file's parent no longer exists. Clearing a file,
closing the workspace, or changing the project also clears `workspacePreview`. Page navigation never carries
file or diff previews into the destination page. Visibility predicates receive `{workspace, projectId}`; workspace can be null
while loading. Unknown view IDs fall back to Files. Extension components use native Vue ESM, without a build step.

File explorer previews use the bundled highlight.js grammars selected by filename/extension, with
Xcode (light) and VS Dark token palettes scoped to `.workspace-source`. They retain the preview's font, wrapping,
spacing, and background instead of adopting Markdown code-block styling. Source is escaped for HTML;
unknown types, files over 200,000 characters, and lines over 8,000 characters fall back to plain text.
Git diffs and Skills previews keep their existing rendering.
The icon at the right of the file breadcrumb toggles line wrapping. Wrapping starts enabled; disabling
it keeps source lines intact with horizontal scrolling. The browser remembers the setting across files
and reloads (`llms.workspace.lineWrap` in localStorage).

PNG, WebP, JPEG, GIF, BMP, AVIF, and ICO files open as image previews, fitted to the available space
on the theme's default background. The authenticated explorer response provides a typed,
base64 data URL in `file.image` plus `file.mimeType`; it never exposes an unscoped filesystem URL.
SVG files include both their source text and image data. They start in render view; the header icon
switches between rendering and highlighted source, where the wrap control remains available. The browser
remembers the SVG view (`llms.workspace.svgView`). Rendering uses an `<img>` element instead of injecting
SVG markup into the page. Decode failures show an error with Retry; changing files clears that error.

Implementation: `llms/ui/modules/WorkspaceSidebar.mjs`, `WorkspaceTreeNode.mjs`, `workspaceTree.mjs`,
`WorkspaceFileView.mjs`, `explorerState.mjs`, `sourceHighlight.mjs`,
`llms/extensions/projects/explorer.py`, `llms/extensions/git/`, `llms/ui/ctx.mjs`, and `llms/extensions/skills/ui/index.mjs`.

Validation: `node tests/test_explorer_state.mjs`, `node tests/test_workspace_tree.mjs`, `node tests/test_git_diff.mjs`,
`node tests/test_source_highlight.mjs`,
`python -m unittest tests.test_workspace_explorer tests.test_git_extension tests.test_git_operations tests.test_git_messages`, and the optional Chromium check
`python tests/verify_explorer_browser.py`. The browser check mounts the real Vue components with fixture
API responses and checks toolbar alignment, folder icons, refresh preservation, root-breadcrumb dropdown keyboard navigation, Git and custom panel registration, disabled Git links, colored diffs, deleted-file previews, diff refresh/close and Back restoration, escaped source text, file highlighting/wrapping, PNG/WebP/JPEG decoding, SVG source/render switching, image-error recovery, selection, Back/Forward, and unsaved-edit guards.
`--fixture git-operations.html` checks editor growth/shrink, theme borders, generated messages with mocked
model responses, manual edits and stale suggestions, staging, staged previews, author entry, failure/retry,
immediate history updates, home repositories, draft isolation, repository submenus, all commit modes,
undo, bulk changes, all stash modes, keyboard/focus behavior, busy/project guards, and submenu placement.
`--dark` checks dark mode and `--width 390` checks the narrow submenu layout.
