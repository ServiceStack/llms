# Git-backed projects: recommended workflow and implementation plan

Status: Phase 1 implemented. Phase 2 staging and committing implemented; initializing existing plain
projects remains planned. Phase 3 push/pull/sync and remote discovery are implemented; a standalone Fetch
action and remote configuration UI remain planned. Phases 4–5 remain planned.

The Git panel now supports explicit stage/unstage (per file or all), staged and working-tree diffs,
commit messages, optional repository-local author configuration, and immediate history refresh.
Unstaged files have a right-click **Discard Changes** action with confirmation, preserving the index and
checking reviewed file/index revisions. Commit rows offer **Open on GitHub**, **Copy Commit Hash**, and
**Copy Commit Message**; menus also support Shift+F10 and arrow keys. GitHub links follow the selected
remote, and message copying includes the complete body.
The branch's ellipsis opens Pull/Push plus Commit, Changes, and Stash submenus. Commit supports the
default staged-or-confirm-all flow, Commit Staged, Commit All, and undoing the last unpublished commit
while preserving staged/working changes. Changes supports stage/unstage all and confirmed discard of
unstaged content while keeping the index. Stash supports tracked, include-untracked, staged (Git 2.35+),
and applying/popping the reviewed latest stash; failed applies keep the stash. These actions validate
reviewed branch/index/worktree state, use the shared mutation locks, and refresh results immediately.
The message editor grows from one line and offers AI generation using the configurable `defaults.commit`
template, supplied with the full staged diff. Suggestions remain editable and do not alter Git state.
This works for a Git project or a repository within the home workspace's allowed directories.
Repository writes coordinate with agent submissions and captured active-run workspaces. Commit
requests have durable receipts and use the reviewed index; retries cannot duplicate a successful commit.

Pull and Push appear beneath the repository label using the supplied icons, with remote selection when
multiple remotes exist, cached ahead/behind counts, loading feedback, and immediate refresh. Pull fetches
the selected upstream branch and fast-forwards only from a clean working tree. Push sends the reviewed
commit to that branch without force or implicit tags; the first sync records an upstream. Local mode
uses existing Git credentials and SSH keys. Hosted mode allows public HTTPS pulls from configured hosts;
push awaits per-user Git authorization. Detached/unborn branches and repositories without remotes keep
the controls visible with explanatory disabled tooltips. Sync uses authenticated workspace resolution,
the same agent submission/repository locks, bounded non-interactive transport, and disabled hooks.
These operations use bounded HTTP requests rather than durable clone jobs; refreshing Git after a lost
response reconciles the result, and push/pull can be retried safely. Branch divergence and content filters
require a Git client. No GitHub MCP is required.

When the working tree is clean and the selected remote has pending incoming/outgoing commits (or the
branch has not been published), the primary Commit button becomes **Sync Changes**, with incoming ↓
and outgoing ↑ counts. Sync fetches and fast-forwards before pushing, under the same repository lock;
divergence stops before the push. A new remote branch is published and given an upstream. Failures
refresh the Git view and preserve any updates already pulled. Working/staged changes restore Commit,
and explicit Pull/Push remain available. Combined sync is local-only because it requires push access.

Phase 1 uses a Projects-owned creation coordinator in `llms/extensions/projects/creation.py`, with
Git command execution supplied by `llms/extensions/git/provision.py`. The durable job database is
`projects/.creation.sqlite` and progress/retry/cancel routes are under `/ext/projects/creation`.
This keeps plain-folder creation and recovery available when the Git extension is disabled. These
names supersede the proposed Git-owned job database and operation routes below for project creation.

Local mode reuses configured Git credentials and SSH identities. Hosted mode initially supports public
HTTPS clones from GitHub, GitLab and Bitbucket, with an administrator-configurable `git_clone_hosts`
list. Per-user hosted credentials and GitHub connection/publishing remain later work. Interrupted
attempts with uncertain child-process ownership retain their isolated temporary folders rather than
risk deleting files still being written.

## 1. Product decision

Keep a project as a local workspace with its own stable identity and chats. Git is an optional
capability of that workspace, and GitHub is an optional remote service. Neither Git nor a GitHub
account should be required to create or use a project.

When the Git extension is available, offer two creation sources initially:

1. **New project**: create a local folder, with **Initialize Git repository** checked by default and
   an opt-out.
2. **Clone repository**: clone a Git repository into a new local project folder, preserving its
   history and remote.

Add **Existing local folder** after defining how external folders fit the workspace permission model.
Offer **Publish repository to GitHub…** from the Git panel later. Creating a GitHub repository at
project creation can eventually be an optional shortcut to that same publishing workflow.

Do not restrict creation to existing repositories, require remote creation, automatically convert
existing projects to Git, or automatically commit an agent's changes.

## 2. Goals and initial scope

- Start a new project offline, with local version control available immediately.
- Create a project from GitHub or another supported Git host using a repository URL.
- Retain the source repository's history, branch, and remote configuration.
- Open the new project through the existing composer/project selection flow.
- Show changes and recent commits through the existing Git workspace panel.
- Keep Git actions hidden when the extension is disabled or Git is unavailable.
- Make long operations recoverable without depending on an open browser connection.
- Preserve existing projects, chat history, drafts, and workspace isolation.

Initial scope is local initialization and cloning. Staging, committing, fetch/pull/push, and GitHub
publishing are subsequent phases. Arbitrary external folders, worktrees, forks, templates, submodule
management, Git LFS installation, conflict editing, force pushes, and automatic synchronization are
not part of the first release.

## 3. Current implementation and constraints

| Area | Current behavior | Required addition |
|---|---|---|
| Projects | Stores stable IDs and metadata in `projects.json`; creates folders on save | A dedicated creation workflow that provisions before registering |
| Workspace resolution | Project folders resolve inside the user's projects directory | Clone into that directory; separately design external folder registration |
| Project manager | New/edit/select flows share the project form and save endpoint | Source selection, Git initialization option, clone progress and recovery |
| Git extension | Optional; disabled without `git` or Projects; initialization, clone jobs, status/history/diffs, local stage/unstage/commit, push and fast-forward pull | Standalone Fetch, remote setup and publishing |
| Git changes | Separate staged and unstaged lists with matching diffs, explicit staging and commits | Hunk-level staging and conflict editing remain later work |
| Git subprocesses | Read-only helper uses a five-second timeout and disables optional locks | A separate operation runner with appropriate locking, progress and cancellation |
| GitHub sign-in | Requests `read:user user:email`; access token is not retained for Git use | Separate repository connection/authorization and credential lifecycle |
| Sidebar | Project updates raise an in-memory change signal | Signal after successful registration; retain existing visibility/draft behavior |

Relevant code:

- [Projects backend](llms/extensions/projects/__init__.py)
- [Project manager](llms/extensions/projects/ui/index.mjs)
- [Git backend](llms/extensions/git/__init__.py)
- [Git UI registration](llms/extensions/git/ui/index.mjs)
- [Git sidebar](llms/extensions/git/ui/GitSidebar.mjs)
- [Workspace sidebar](llms/ui/modules/WorkspaceSidebar.mjs)
- [GitHub sign-in](llms/extensions/github_auth/__init__.py)
- [Chat/project invariants](docs/CHAT_THREADS.md)

The current project persistence helpers include cross-process file locking and atomic writes. Reuse
these mechanisms for short metadata transactions; do not hold their lock throughout a network clone.

## 4. Project creation UI

### 4.1 New project

Keep the existing name, folder, description, sidebar visibility, and build publish directory fields.
Add a compact source choice only while creating, not while editing an existing project.

When Git initialization is available, show:

> [x] Initialize Git repository

The initial branch should be `main`, with compatibility handling for Git versions that lack
`git init --initial-branch`. Repository initialization must not require a configured commit author.
Do not silently create a README, choose a license, or add broad ignore rules. Future starter/template
options can explicitly generate those files.

Button: **Create project**.

Creation with Git selected succeeds only once both the local folder/repository and project
registration are ready. If initialization fails, show the reason and let the user retry or explicitly
create without Git. Do not silently drop their selection.

Existing project edits must never reinitialize Git or trigger network operations. Existing plain
projects can later expose **Initialize repository** in the Git panel.

### 4.2 Clone repository

Show these fields:

| Field | Behavior |
|---|---|
| Repository URL | Required; accept supported HTTPS and SSH Git URLs |
| Project name | Derive from the repository name; remain editable |
| Local folder | Derive from the name; remain editable; resolve within the user's project root |
| Branch | Optional advanced field; empty means the remote's default branch |
| Description | Optional; do not require a GitHub API lookup |
| Destination preview | Show the resolved server-side location before starting |

A normal GitHub repository URL should work directly. Normalize the optional `.git` suffix where
appropriate, but do not guess a clone URL from arbitrary GitHub issue, pull request, or file URLs.
GitHub shorthand such as `owner/repository` can be added as an explicit convenience later.

Button: **Clone & create project**.

Use a regular clone with history, without a default shallow depth. Preserve the checked-out branch
and remote, normally `origin`. Do not recursively initialize submodules, install dependencies, run
project scripts, or launch an agent as part of cloning. Repositories requiring LFS or submodule
checkout should receive a clear follow-up message when relevant.

Progress belongs to the originating creation request. The user can leave the dialog and return to
the operation. Completion must not move a different chat or overwrite a newer project selection.

### 4.3 Completion and sidebar behavior

After successful registration:

1. Update project state and raise the existing sidebar signal.
2. If creation began from a composer project picker, invoke its existing callback with the stable
   project ID; apply it only to the originating draft/thread under the current membership rules.
3. Otherwise, use the existing `openDraft(project.id)` flow to start or restore a draft in the project.
4. Make the Files/Git workspace panel available without a full-page reload.

Do not change `lastActivityAt`, canonical messages, or thread ordering because a repository was
created. Project visibility continues to follow the existing sidebar/draft rules.

## 5. Ownership and extension boundaries

**Projects owns** project identity, metadata, destination validation, folder registration, workspace
resolution, sidebar visibility, and creation callbacks.

**Git owns** Git discovery, repository initialization/cloning, subprocess execution, repository state,
operation jobs, staging/committing, and remote transport operations.

**GitHub integration owns** account connection, repository lookup/creation, owner and visibility choices,
and GitHub-specific publishing. It should register a capability with Git rather than make Projects
depend on GitHub sign-in.

Expose shared backend service interfaces through the extension context. Discover optional providers
after extension installation rather than relying on import or installation order. Projects should use
a small Git provisioning interface, not construct Git commands itself.

Frontend actions should use advertised capabilities, not infer support from a registered icon or the
presence of a Git executable alone. Enforce those capabilities again on the server.

Retain native Vue ESM and the current asset workflow. Use Python standard library and `aiohttp`;
do not add a Git Python library, a bundler, or a required `gh` dependency.

## 6. Proposed service and API contract

These route names and payloads are proposals. Final implementation should follow the repository's
existing response/error conventions and may adjust names without changing the behavior.

| Route | Purpose |
|---|---|
| `GET /ext/git/capabilities` | Available initialization, clone, mutation and GitHub features |
| `POST /ext/projects/create` | Validate a new project and start provisioning/registration |
| `GET /ext/git/operations/{id}` | Read an operation's current state and safe progress summary |
| `GET /ext/git/operations/{id}/events` | Subscribe to progress/completion with reconnect support |
| `POST /ext/git/operations/{id}/cancel` | Request cancellation of a cancellable operation |
| `POST /ext/git/repositories/init` | Initialize an existing owned plain project, later phase |
| `POST /ext/git/repositories/stage` | Stage selected paths, later phase |
| `POST /ext/git/repositories/unstage` | Unstage selected paths, including before the first commit |
| `POST /ext/git/repositories/commit` | Commit the current index with an explicit message |
| `POST /ext/git/repositories/fetch` | Fetch remote state |
| `POST /ext/git/repositories/pull` | Initially fast-forward-only pull |
| `POST /ext/git/repositories/push` | Push selected branch to an explicit remote |
| GitHub provider routes | Connection, owners/repositories, creation and publish orchestration |

Example creation request:

```json
{
  "requestId": "browser-generated-uuid",
  "project": {
    "name": "Example",
    "folder": "example",
    "description": "",
    "showInSidebar": true,
    "publish": ""
  },
  "source": {
    "kind": "clone",
    "url": "https://github.com/example/example.git",
    "branch": null
  }
}
```

For a new folder, use `source.kind = "new"` with `initializeGit: true` or `false`.
For first release cloning, omit GitHub-specific choices from this request entirely.

Return an operation ID and state, normally with HTTP 202 for asynchronous provisioning. The operation's
completion response includes the persisted project ID and refreshed metadata. A request ID is scoped
to the authenticated user and makes repeated submissions return the same operation; reuse with a
different payload returns a conflict.

Mutation requests identify an owned project by stable ID. Resolve its repository server-side; do not
accept an arbitrary absolute repository path as authorization. Existing read-only explorer routes can
retain their browsing contract.

Use structured errors for invalid source/destination, destination conflict, Git unavailable,
authentication required, operation conflict, timeout, and unsupported repository state. Return sanitized
details that help recovery rather than raw command output.

## 7. Persistence and operation lifecycle

### 7.1 Project metadata

Keep existing project IDs and fields. Add only optional provenance metadata, for example:

```json
{
  "gitSource": {
    "kind": "clone",
    "url": "https://github.com/example/example.git",
    "branch": null
  }
}
```

This records how the project was created. It is not the authoritative current remote/branch state;
read that from Git configuration when displaying repository state. Store no embedded credentials.

Do not reuse `publish` or `publishedUrl`: those fields describe published build output, not GitHub
repository publishing. A future GitHub association can use a separate optional field, with repository
identity confirmed through the provider rather than trusted from client input.

### 7.2 Durable jobs

Use a small Git extension-owned per-user SQLite database, proposed as `git.sqlite`, for operation state.
Keep Git operations separate from agent runs: cloning has no model messages, compaction, or agent
execution semantics. Use standard-library `sqlite3` and additive schema migrations.

Persist operation ID, owner, request ID, payload fingerprint, kind, project/reserved destination,
job-owned temporary location, timestamps, lease/heartbeat, cancellation request, safe progress,
result, and sanitized error. Do not persist tokens or unredacted transport logs.

States:

```text
queued → running → finalizing → succeeded
           ├───────────────→ failed
           ├───────────────→ cancelled
           └───────────────→ interrupted
```

Use bounded workers and per-repository/destination serialization across processes. Keep metadata
locks short. Progress subscriptions should be event-driven; reconnect reads a persisted snapshot
before subscribing. If SSE is unavailable, bounded long-poll waiting is acceptable; avoid idle polling.

### 7.3 Clone/folder transaction

Filesystem provisioning and `projects.json` cannot form one atomic transaction. Make the sequence
explicit and recoverable:

1. Validate ownership, name, source, and destination; reject duplicate names/folders server-side.
2. Reserve the normalized destination for the operation under a short cross-process lock.
3. Provision in a job-owned temporary sibling directory on the same filesystem.
4. Validate the resulting workspace/repository and mark the operation `finalizing`.
5. Recheck the reservation and destination; promote without overwriting an existing directory.
6. Register the project with a server-generated stable ID under the project metadata lock.
7. Persist the result, notify the sidebar, and release the reservation.

Record finalization steps so recovery can distinguish a completed directory from a registered project.
If metadata registration fails after promotion, retain the usable workspace and offer retry/recovery;
do not delete completed user files. Retrying must not clone again or create a second project ID.

Cancellation stops the subprocess and its children, then removes only temporary files provably owned
by that operation. Finalization is a short non-cancellable boundary. Never delete a pre-existing folder
or a completed workspace as automatic error cleanup.

On restart, reclaim expired jobs under a lease. Mark interrupted network work visibly and offer retry;
do not assume a half-finished clone is usable. Reconcile interrupted finalization idempotently. An
orphan child process must be accounted for before a replacement operation writes the same destination.

## 8. Git execution, authentication, and permissions

### 8.1 Subprocess runner

- Execute argument arrays without a shell; validate options, refs and paths independently.
- Keep read-only and mutating runners separate. Do not inherit `GIT_OPTIONAL_LOCKS=0` blindly for writes.
- Disable interactive terminal/askpass prompts unless the controlled credential provider supplies them.
- Stream bounded, sanitized progress; Git clone commonly reports progress on stderr.
- Use configurable operation deadlines and inactivity handling appropriate to network operations.
- Support process cancellation on Unix and Windows, including child-process cleanup.
- Bound concurrent jobs per user and globally; serialize writes to the same repository across processes.
- Disable checkout hooks and avoid executing untrusted filter/helper configuration during provisioning;
  account for Git templates and inherited configuration without breaking authorized credential access.
- Report missing LFS/filter requirements rather than installing or executing repository-provided tooling.

### 8.2 Source and destination validation

Initially support HTTPS and SSH URLs, including conventional SCP-style SSH syntax. Reject embedded
passwords/tokens, command-like arguments, unsupported transport helpers, and arbitrary local/file
sources through the remote-clone form. Do not turn an arbitrary URL into a shell fragment.

Canonicalize destination paths using cross-platform real-path and case normalization. Reject traversal,
symlink escapes, the projects root itself, occupied destinations, and case-equivalent collisions where
the filesystem is case-insensitive. Mutations must remain inside the owned workspace, including Git
metadata: validate `.git` directories/files and reject unsupported external gitdir/worktree layouts.

Remote network access occurs on the server hosting llms, not in the browser. A desktop/local install
can use its configured Git hosts and credentials. A hosted multi-user deployment needs an explicit
remote-host/network policy, including private Git servers when administrators authorize them; URL
validation alone does not provide that policy.

### 8.3 Credentials

Public HTTPS clones should need no GitHub connection. Private clones require a usable credential
provider or SSH identity and should return **Authentication required** when none is available.

For a single-user desktop/local deployment, reuse configured system Git credentials/SSH where allowed.
For a hosted multi-user deployment, use credentials scoped to the authenticated user; do not borrow
the server operator's global Git or `gh` account. Keep secrets out of URLs, `projects.json`, browser
storage, operation records, process arguments and diagnostic logs.

Use the existing credentials infrastructure only after verifying its storage and isolation guarantees;
otherwise extend it explicitly. Present connection identity and permission errors without exposing
the underlying credential.

### 8.4 Commit identity and active runs

Initialization and cloning do not require an author. Committing does: use repository/user Git identity
when configured, or ask for name/email and an explicit persistence scope. Do not silently infer an
author from the llms account or GitHub sign-in profile.

Before enabling mutations on an existing project, add a shared coordination mechanism with agent
runs. Reject Git writes while a project has queued/running/approval-waiting runs and prevent a new run
from starting during a conflicting repository mutation. A one-time preflight check is insufficient
because a run could be queued immediately afterward.

Read-only history and diffs remain available. Do not change a run's captured workspace or canonical
messages to accommodate repository operations.

## 9. Staging, commits, and synchronization

Add distinct **Changes** and **Staged changes** sections. A file may appear in both when the index and
working tree differ. Diff controls must compare the appropriate pair: HEAD/index or index/working tree.
Keep the existing commit expansion and historical diff behavior.

Support stage/unstage per file and an explicit stage-all action, handling additions, deletions, renames,
unborn HEAD, and literal filenames. Validate paths and use explicit path separators in Git arguments.
Commit only the current index, require a message, and show clearly which files are included. Do not
automatically stage all files as an implicit side effect of Commit.

A newly initialized empty repository has no commits. Show **No commits yet** and untracked files as
additions. If starter files are generated later, offer an explicit initial commit; do not manufacture
an empty history entry just to populate Recent commits.

Introduce Fetch, fast-forward-only Pull, and Push with explicit remote/branch selection and visible
ahead/behind state. A rejected push or non-fast-forward pull should explain the next step without
forcing, resetting, or automatically rebasing. Preserve local files and commits on network failures.
Branch switching, merge/rebase, conflict editing, and destructive operations need separate designs.

## 10. GitHub publishing

### 10.1 Separate connection

Treat **Connect GitHub** as optional repository authorization, separate from signing into llms. A
signed-in GitHub user does not automatically have repository access in the current implementation.
Request the permissions needed for the selected feature, retain credentials server-side through the
chosen credential store, and support reconnect/revoke.

The native implementation can call GitHub REST APIs through `aiohttp`; `gh` may be an optional desktop
adapter but must not become a required runtime dependency. Decide on the GitHub authorization mechanism
before this phase: GitHub App, OAuth connection, or explicitly configured tokens, including organization
policy support. Do not increase the existing sign-in scopes as an undocumented side effect.

### 10.2 Publish flow

From the Git panel, offer **Publish repository to GitHub…** when there is a local Git repository and
the GitHub publishing capability is available.

1. Connect/select an account and owner/organization.
2. Enter repository name and description; select visibility, defaulting to private.
3. Review the branch and committed content being pushed. Explain that uncommitted files are excluded.
4. Require at least one commit for the initial publish-and-push workflow; provide the normal commit
   flow if needed.
5. Create an empty remote repository, without separately generated README/license/ignore commits.
6. Add the remote and push the chosen branch with upstream tracking.
7. Show the repository link and retain normal Git synchronization controls.

Never overwrite an existing `origin` silently. If a remote exists, distinguish pushing to it from
creating an additional repository; ask for the intended remote name or defer to an explicit connect
remote flow. Cloning another person's project does not imply permission to push or a request to fork.

Persist remote creation before pushing. If creation succeeds but push fails, retain the returned
repository identity and local project, and offer **Retry push** rather than creating another remote.
If a response is lost, verify repository identity/ownership and operation provenance before reusing
it; a matching name alone is insufficient. Never automatically delete a remote repository on failure.

### 10.3 Creation-time shortcut

After publishing is established, a new-project form can offer **Also create on GitHub** as an optional
advanced choice. Reuse the same owner, visibility, connection and recovery flow. Local project creation
must remain usable when remote creation fails; clearly report local success and remote failure.

For an empty new project, either wait until the first commit or explicitly offer starter files and an
initial commit. Do not hide the need for committed content by silently adding files.

## 11. Existing local folders: later design

The current project folder field is not an arbitrary external workspace path. Keep that invariant in
the initial release rather than adding an absolute path to `folder`.

When this source is implemented, choose explicitly between:

- Registering an unregistered existing folder already inside the user's managed project root.
- Copying/importing an external folder into the managed root.
- Registering an external workspace with a new validated location type and ownership/permission rules.

External registration requires changes to workspace resolution, agent sandboxing, explorer roots,
project rename/delete behavior, and configuration portability. The UI must identify that the path is
on the server, not the browser's machine. Detect existing repositories and avoid nested initialization.
External worktrees, shared repositories, and `.git` links require explicit support, not permissive
path handling. Removing a project entry should not imply deleting an external folder.

## 12. Delivery phases and acceptance criteria

### Phase 1 — Local initialization and remote cloning

Implement capability discovery, the Projects creation service, Git provisioning jobs, new/clone source
UI, managed destination validation, idempotency, progress/cancel/recovery, and project completion wiring.
Use already configured authorized credentials; introduce clear authentication-required errors without
making a GitHub browser connection a prerequisite for public cloning.

Accept when:

- Plain project creation works with Git disabled, missing, or explicitly unchecked.
- A new Git project opens on `main` without requiring author identity or producing a fake commit.
- A public repository clones with history, optional branch selection, and correct remote state.
- A private repository works through the selected credential provider or fails with a clear action.
- Failure/cancellation preserves existing folders and creates no successful project entry prematurely.
- Duplicate submissions, concurrent destination claims, restart and interrupted finalization recover
  without duplicate projects or overwritten files.
- Sidebar and Files/Git views update without a page reload; the origin draft/chat remains correct.

### Phase 2 — Local repository operations

Add Initialize repository for existing managed plain projects, staged/unstaged state and diffs,
stage/unstage, author configuration, commit UI, repository locks, and coordination with active runs.

Accept when index/worktree distinctions, partial staging, deletion/rename paths, unborn HEAD, and
commit failures are correctly represented; only staged content is committed and new history appears
immediately. Agent activity and repository mutations cannot race through an authorization preflight.

### Phase 3 — Remote synchronization

Add remote discovery/connection, Fetch, fast-forward-only Pull, Push, branch/upstream display and
ahead/behind state. Reuse credential providers and operation infrastructure.

Accept when network/authentication failures preserve local work and divergence is surfaced without
implicit force pushes, resets, rebases, or discarded changes.

### Phase 4 — GitHub connection and publishing

Implement the chosen GitHub credential/authorization model, owner selection, private-by-default remote
creation, publish review, remote association, push and recovery. GitHub repository browsing can then
enhance Clone without replacing generic URL entry.

Accept when sign-in and repository authorization remain distinct, user credentials stay isolated,
organization restrictions are visible, existing remotes are preserved, and retries reuse a confirmed
created repository after a failed push.

### Phase 5 — Optional conveniences

Consider existing folder registration, create-on-GitHub during creation, repository picker, explicit
fork/template workflows, starter files/initial commits, submodules/LFS, and branch/conflict operations.
Each should use the established project, permission, and job contracts rather than create a parallel
creation or publishing implementation.

## 13. Validation plan

Use temporary repositories and a local bare remote for deterministic tests. Mock GitHub HTTP responses;
do not require live repositories, credentials, or destructive remote operations in automated tests.

Backend coverage should include:

- Git unavailable/extension disabled; new plain and initialized projects.
- Normal/root/empty repository clone, branch selection, missing branch and inaccessible remote.
- Ownership, destination traversal, symlink escape, case collisions and unsupported Git metadata links.
- Literal unusual filenames, URL/ref argument injection, credential redaction and user isolation.
- Idempotency and competing create requests across processes.
- Subprocess failures/timeouts/cancel, worker lease expiry, restart and finalization fault injection.
- Atomic metadata writes, stable IDs, sidebar signalling and unchanged canonical conversation history.
- Staged-only and mixed changes, unborn HEAD, author errors, deleted/renamed files and index contention.
- Agent-run/mutation race prevention, rejected pushes and non-fast-forward pulls.
- GitHub name conflicts, organization permission failures, creation success followed by push failure,
  lost-response recovery and reconnect/revoke.

Browser coverage should use the existing explorer/project fixtures and real components. Verify source
switching, defaults, keyboard/focus behavior, duplicate submit prevention, progress/cancel/retry,
dialog reopen, completion after navigating away, correct origin callbacks, disabled capabilities,
and sidebar/Git refresh without full reload.

Keep tests proportionate to each delivered phase; do not implement or test later-phase behavior in
the first release merely because it is described here. Rebuild generated CSS when UI classes change.

## 14. Compatibility and rollout

- Preserve existing project metadata and save/edit endpoints; do not migrate plain folders to Git.
- Route new provisioning through the dedicated creation endpoint; editing stays a metadata operation.
- Ensure later bulk saves/edits preserve new optional provenance fields rather than dropping them.
- Advertise only implemented backend features, so shared UI does not assume every host supports them.
- Review the C# AI.Chat port before syncing frontend changes: hide unavailable creation/mutation
  features until its backend contract exists. Python implementation does not imply C# support.
- Document supported Git versions/transports, server-side paths, credentials, limits and recovery.
- Leave users' repositories usable through ordinary Git outside llms.

## 15. Decisions to settle during implementation

The recommended defaults are settled above. These details need targeted implementation review:

- Minimum Git version and fallback behavior for branch initialization.
- Worker concurrency, deadlines, progress retention and completed-job cleanup limits.
- Credential provider support in desktop versus hosted deployments, and allowed network policy.
- Whether initial branch preference should become a user setting beyond the default `main`.
- Exact active-run/repository-write coordination primitive and its cross-process behavior.
- GitHub authorization mechanism and organization support before Phase 4.
- External workspace ownership/location model before offering arbitrary existing folders.

## 16. Primary references

- [Git clone documentation](https://git-scm.com/docs/git-clone): repository cloning, default remote,
  branch selection and transport behavior.
- [GitHub CLI repository creation](https://cli.github.com/manual/gh_repo_create): creation from an
  existing local repository and pushing committed content.
- [GitHub repository creation API](https://docs.github.com/en/rest/repos/repos#create-a-repository-for-the-authenticated-user):
  repository creation parameters and required authorization.

These sources establish what Git/GitHub support. The UI defaults, extension boundaries, phased delivery,
and recovery semantics in this document are recommendations for llms.
