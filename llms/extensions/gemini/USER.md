# Gemini RAG

Gemini RAG lets you build searchable document collections with Google Gemini File Search, keep
them organised with categories and metadata, and start grounded chats over all or part of a
collection. Answers include inline citations and a source list linking back to the original pages
when a Source URL is available.

Use it when you want to ask questions over product documentation, policies, source repositories,
knowledge bases, or another collection that is too large or changes too often to paste into a
chat.

## Before you start

The Gemini extension needs a Google API key. Set either variable in the server environment or its
`.env` file:

```bash
GOOGLE_API_KEY=your_api_key
# or
GEMINI_API_KEY=your_api_key
```

`GOOGLE_API_KEY` takes precedence when both are set. The Gemini icon is unavailable when neither
key is configured.

You also need at least one Google Gemini chat model configured in llms.py. The model picker in the
Gemini page lists only usable Google chat models. A model selected there is remembered as the
Gemini File Search override; clear the override to return to the normal Gemini default.

## The basic model

A **File Store** is one searchable corpus in Gemini. It is also the practical security and
ownership boundary: put documents that should be searched and administered together in the same
store, and use separate stores for collections that should remain separate.

Each store has two main views:

- **Explore** is the everyday document browser. It contains categories, search, filters, metadata
  editing, upload state, coverage, synchronisation, and chat shortcuts.
- **Import** adds documents. It supports direct file uploads and repeatable imports from folders on
  the server.

The extension keeps a local catalogue and cached copy of each document while Gemini holds the
indexed copy used for retrieval. Upload and sync states describe the relationship between those
two copies.

## Quick start

1. Open the Gemini icon in the left toolbar.
2. Select **New File Store**, enter a name, and create it.
3. Open the store and select **Import**.
4. Drop in one or more files, optionally choose a destination category and metadata, then select
   **Upload N files**.
5. Follow **View uploads** to watch the documents move through the upload queue.
6. Return to **Explore** and select **New Chat** to query the whole store, or build a filtered view
   and select **Ask about this**.

Creating and deleting stores changes both the local catalogue and Gemini. Deleting a store is
permanent and removes all of its documents.

## Importing files

The **Upload files** tab accepts multiple files by picker or drag and drop. The UI accepts:

- PDF
- Markdown and MDX
- plain text
- HTML
- reStructuredText and AsciiDoc
- CSV and JSON
- YAML
- ZIP archives containing supported files

Direct uploads are queued immediately. Gemini performs the indexing in the background; the import
status reports progress as `Uploading 16/21 documents to <store>…`. **View uploads** switches to
Explore at the destination category and sorts by **Uploading**. The view continues updating while
work is in progress.

### ZIP archives

A ZIP is expanded and each supported entry becomes its own document. Its internal folders become
categories. If you set a destination category, the ZIP's structure is nested underneath it.

For example, `guides/auth/login.md` uploaded into `products` becomes:

```text
products/guides/auth/login.md
```

macOS metadata, hidden folders, dependency folders, build output, lock files, and other standard
noise are excluded.

### Importing into the category you are viewing

Select **Import here** while browsing a category. The Import tab opens with **Destination
category** already populated. For file uploads this is the category itself; for folder imports it
is a prefix under which the folder's own structure is preserved.

## Importing a folder

### Staging a website crawl

The **Web crawl** import tab converts a website into an inspectable folder before anything is
sent to Gemini. Enter a start URL and the folder name is pre-populated from its host. Ports use a
dash, so `http://localhost:5000` becomes `localhost-5000`. You can override the name before the
crawl starts.

Crawls are private to the current user and are stored beneath:

```text
users/<user>/gemini/imports/<domain>/
```

Ordinary page URLs are written as clean Markdown paths: `/docs/templates/next-rsc` becomes
`docs/templates/next-rsc.md`. The site root is written to `index.md`, while URLs ending in `/` use
an `index.md` in that directory (`/docs/` becomes `docs/index.md`). Each file starts with frontmatter
containing its title, public source URL, path, query string, meta description and any page tags.
URLs that differ only by query string receive a stable suffix so they cannot overwrite one another.

Crawl imports appear alongside folder imports in **Saved imports**. Select **Edit** to adjust
crawl rules and ordered regular-expression transforms in the Web crawl form. After crawling,
select **Import folder** to open the Folder form with the generated Markdown folder and saved
settings already loaded. Add attributes, preview the documents, and select **Run import** there.
**Run** in the saved list imports its current files, and
**Sync Store** refreshes the crawl and imports changes.

### Controlling what is crawled

The Web crawl form provides the common safety controls directly:

- **Include paths** and **Exclude paths** use the same glob syntax as folder imports. A crawl
  started at `/docs/` is also confined to that path, so `/docs-private/` is not accidentally
  included.
- **Query strings** defaults to Ignore. Allow selected retains only named parameters; Include all
  retains every parameter except common tracking/session parameters. Query keys are sorted before
  deduplication and each path is limited to five variants by default.
- **Max depth** limits link traversal and **Max pages** limits saved documents. A separate request
  ceiling prevents a large set of follow-only pages from bypassing the page limit.
- Crawls are same-origin by default. **Additional hosts** permits named supporting hosts without
  opening the crawler to arbitrary external links.
- `robots.txt`, page `noindex`/`nofollow`, link `rel=nofollow`, canonical URLs, HTML content types,
  and duplicate extracted content are handled by default. Each behavior can be changed in the
  crawl form or its saved manifest.

Advanced ordered rules support `exclude` (do not fetch or follow) and `followOnly` (discover its
links but do not write a Markdown file). The first matching rule wins. The Web crawl tab renders these
rules from the server's JSON Schema, with separate Path rule and Query-string rule forms:

```json
{
  "crawl": {
    "include": ["/docs/**"],
    "exclude": ["/docs/archives/**", "/account/**", "/search/**"],
    "query": {
      "mode": "allow",
      "allow": ["version", "lang"],
      "exclude": ["utm_*", "fbclid", "gclid", "ref", "session", "token"],
      "maxVariantsPerPath": 5
    },
    "maxDepth": 10,
    "maxPages": 500,
    "sameOrigin": true,
    "allowedHosts": ["cdn.example.org"],
    "respectRobots": true,
    "respectNoIndex": true,
    "followNoFollow": false,
    "useCanonical": true,
    "dedupeContent": true,
    "contentTypes": ["text/html"],
    "rules": [
      { "match": "/docs/sitemap/**", "action": "followOnly" },
      { "queryString": true, "action": "exclude" }
    ]
  }
}
```

### `import.json`

A folder or ZIP may contain `import.json` manifests. The root manifest supplies the global import
configuration; a manifest in a subdirectory inherits it and overwrites metadata for files below
that directory. Page frontmatter is more specific and overwrites inherited defaults. Metadata
entered explicitly in the Import UI has the highest precedence.

```json
{
  "version": 1,
  "metadata": {
    "defaults": { "product": "ServiceStack", "tags": ["docs"] },
    "rules": [
      { "match": "auth/**/*.md", "set": { "tags": ["auth"] } }
    ]
  },
  "transforms": [
    {
      "match": "**/*.md",
      "pattern": "\\nEdit this page.*$",
      "replacement": "",
      "flags": "gim"
    }
  ]
}
```

When no metadata has been entered, Preview Import automatically loads the root `import.json`.
Previewing a folder saves its input settings to the manifest atomically,
preserving its crawl, transforms, and inherited metadata. Click **Load import.json** in **Saved imports**
to browse folders on the server and select a manifest, or enter its path and click
**Load**. Folder manifests open in **Folder**; manifests with a crawl URL open in **Web crawl**.
Both use the same `import.json`, with crawl rules, transforms, folder settings, and metadata
stored together. Loading immediately adds the import to the store's **Saved imports** list and
opens its editor without uploading documents. Use **Run**, **Preview**, or **Sync Store** when ready.
Existing crawl workspaces can be added by selecting their `import.json` here.

Loading a web crawl from outside your own `gemini/imports` directory copies its workspace,
including `import.json`, generated Markdown, and nested metadata, into your private imports
directory under its plain crawl name, such as `sharpscript.net`. The original workspace is left
untouched. Loading that crawl again refreshes the same private folder with the selected manifest
and files, retaining the saved import and document identities. No suffixed duplicate folder is created.
**Crawl website**, transforms, page browsing, Preview, Run, and Sync Store all use the saved copy.
Recrawling an edited import saves its settings and keeps the same saved import and document identities.
It replaces generated pages with the latest website content and removes generated pages that are
no longer present. Running the folder import then syncs those changes to Gemini. Repeating the same
crawl and import leaves the store unchanged and queues no new embeddings after successful uploads.

Click **Edit** on a saved import to load its current manifest into the same form. **Save changes**
updates that source's `import.json` and keeps its existing documents and identity. For web imports,
the editor opens directly in **Web crawl** for crawl rules and transforms. **Edit crawl settings**
switches back there from the Folder form;
**Edit folder settings** returns to the folder settings. Saving settings does not run an import or
spend on embeddings. A store can have multiple saved imports.

**Preview import** scans the input and shows proposed changes without indexing documents.
In Web crawl, **Import folder** continues into the Folder form to configure attributes before
importing the generated Markdown files. **Run import** in that form applies changes and queues
uploads. Preview and Run save the current form settings to the same import first, preserving
the crawl settings when switching between forms.
**Unchanged locally** means the source content matches the local cache. These files can still be
**Awaiting Gemini upload**; Preview offers **Resume** and Run resumes their existing queue entries
without creating duplicate documents. Upload progress is scoped to this import. It shows **Queued**
while earlier work is running and a completion count once uploads begin. A run with no source
changes or pending uploads reports that directly.
The editor stays open after running. **Close editor** clears the form to start another source;
the saved import remains in the list. **Save changes** and **Close editor** appear below the
Preview and Run actions.

The portable manifest stores input settings under `source`, with metadata alongside them:

```json
{
  "version": 1,
  "source": {
    "name": "Product docs",
    "type": "folder",
    "config": {
      "path": ".",
      "include": ["**/*.md", "**/*.html"],
      "ignore": ["drafts/", "private.md", "**/generated/**"],
      "requireSourceUrl": false
    },
    "category": { "root": "docs", "maxDepth": 4, "prefix": "product" },
    "extract": { "minWords": 25 },
    "onDelete": "tombstone"
  },
  "metadata": { "defaults": { "product": "My product" }, "rules": [] }
}
```

Input paths are relative to the manifest's directory; `.` imports that directory. Absolute paths
are also supported. `include`, `exclude`, and `ignore` accept arrays of paths or globs (the UI
accepts comma or newline separated entries). A folder entry such as `drafts/` ignores its entire
subtree. Nested manifests can add `ignore` or `exclude` patterns relative to their own directory
and override metadata. Existing metadata-only manifests remain supported.

**Sync Store** reloads every enabled saved import's manifest, adds new files, queues replacements
for changed content or metadata, and applies the source's deletion policy. Web imports refresh
their crawl using the saved crawl rules and reapply saved transforms. Uploads run in the background;
source errors and queued uploads appear in the sync results. Unchanged sources require no new
uploads. Large deletions still require confirmation through the individual source run. Older
saved folder imports gain a manifest link on their next successful sync.

The **Folder** tab scans a directory on the machine running llms.py. It is useful for documentation
repositories and other collections you want to preview and re-run as they change.

| Setting | Meaning |
| --- | --- |
| **Folder path** | Directory to scan. Non-admin users are restricted to trusted import folders. |
| **Category root** | Only import files beneath this subfolder and remove that prefix from derived categories. |
| **Max depth** | Limit how deeply files are imported. Use `0` for files directly in the selected directory only, `1` to also include files in its immediate subdirectories, or leave blank for unlimited depth. |
| **Include only** | Optional glob such as `**/*.md`. |
| **Exclude** | Optional glob such as `**/drafts/**`. |
| **Ignore files and folders** | Paths or globs such as `drafts/, private.md`; folder paths exclude their entire subtree. |
| **Destination category** | Optional category prefix for the entire import. |

Folder imports currently extract UTF-8 or Latin-1 text from text, Markdown, HTML, common source
code, and configuration formats. HTML is converted to readable text. PDF, DOCX, PPTX, and XLSX
files found during a folder scan are reported as unsupported and skipped; upload those files
directly when you want Gemini to index them.

Very short prose files are skipped by default because navigation fragments and boilerplate usually
hurt retrieval quality. Short source-code files are retained.

### How categories are derived

Categories come from the document's directory, not its filename. Given this source:

```text
docs/guides/auth/login.md
```

with a Category root of `docs` and no destination prefix, the document lands in
`guides/auth`. With a destination of `products`, it lands in `products/guides/auth`.

Category root also limits the import: files outside that subfolder are skipped. Max depth is
measured from Category root when one is set, otherwise from Folder path. Use a Max depth of `0`
to import only files directly inside that directory, with no files from subdirectories. A Max
depth of `1` includes direct files plus files in immediate child directories.

## Previewing and confirming an import

Folder imports always begin with **Preview import**. Previewing scans and compares the source but
does not write documents, upload data, or incur embedding work. Review these figures before
confirming:

| Result | Meaning |
| --- | --- |
| **Discovered** | Files considered by the run. |
| **New** | New documents that will be indexed. |
| **Changed** | Existing documents whose extracted content changed. |
| **Metadata only** | Content is unchanged but indexed metadata changed. |
| **Unchanged** | Documents that require no work. |
| **Removed** | Previously imported documents no longer present upstream. |
| **Skipped** | Unsupported, excluded, outside the category root, or too short. |
| **Failed** | Files that could not be read or extracted. |
| **Embeds** | Documents that will be uploaded and embedded if confirmed. |

The preview also shows sample derived metadata, expanded Source URLs, rule match counts, and sample
skip/failure reasons. Select **Import N documents** only after the preview looks right.

A run that would remove an unusually large portion of a source is refused by a deletion safety
rail. This protects against a mistyped path, moved site, or incomplete listing looking like a
legitimate mass deletion.

## Saved imports

Folder imports are always saved when previewed, and web crawls are saved after crawling.
Folder and web crawl imports share the **Saved imports** list. Give each import a unique name
within the store. Dismissing a preview keeps the saved import so it can be run later.

For a saved import:

- **Edit** opens the existing Folder or Web crawl form with its manifest settings.
- **Run** imports its current files into this store.
- **Preview** rescans its files without changing documents.
- **Import N documents** applies the displayed changes.
- **Remove** stops syncing the source in this store. Its `import.json`, source files, and previously
  imported documents are kept. Load the manifest again to save the import again.

The manifest saves the destination category, category root/depth, file filters and ignores,
metadata, extraction settings, crawl rules, and transforms. Changing the selected store category
does not overwrite a loaded import's destination. Removing and reloading the same manifest in
the same store reconnects its existing documents, preserving their local and Gemini identities.

Re-running compares normalised extracted content and metadata independently. Unchanged files are
not re-embedded. Content changes and metadata-only changes both require a new Gemini embedding,
because Gemini cannot patch indexed metadata in place.

Repeated previews leave documents unchanged. Repeated runs reuse the document's identity within
the saved import, so running again while uploads are pending does not create another document.
Once uploads finish, an unchanged input reports zero new embeddings. Changed content, metadata,
and document titles replace the existing remote copy after the new copy succeeds. If removing an
upstream document fails, its identity is retained, the error is reported, and the next run retries
the removal. Re-running also requeues failed uploads on their existing document rows. Sync Store
audits remote drift across every enabled saved import.

When an upstream file disappears, the default behavior removes its Gemini copy and leaves a local
`removed upstream` tombstone so the change remains visible.

## Trusted import folders

Reading a server-side folder is a privileged operation. When authentication is enabled:

- Admins may import from any folder.
- Other users may import only from the union of the server's allowed directories and the Gemini
  trusted import folders.

The **Trusted import folders** panel shows the effective configuration. Admins can add or remove
one path at a time; changes are saved immediately. Paths may use `~`, `$WORKSPACE`, and `$TEMP`.
The UI shows the resolved path and warns about missing or very broad roots.

The same setting can be managed in the deployment-wide `config.json`:

```json
{
  "gemini": {
    "importRoots": ["$WORKSPACE/docs", "/srv/knowledge"]
  }
}
```

Real paths are checked, so a symlink inside an allowed root cannot be used to read outside it.

## Metadata

Metadata improves browsing and lets a chat search only the documents relevant to a question.

| Field | Use |
| --- | --- |
| **Category** | Hierarchical location in Explorer. Derived during import, but editable later. |
| **Doc type** | Content kind, such as `guide`, `reference`, `api`, `faq`, `release-notes`, `policy`, or `changelog`. |
| **Status** | Lifecycle, such as `published`, `draft`, `deprecated`, or `archived`. |
| **Product** | Product or component the document belongs to. |
| **Locale** | Language or locale, for example `en` or `en-AU`. |
| **Versions** | One or more applicable versions. Each comma-separated value is stored separately. |
| **Tags** | One or more free-form labels. Each comma-separated value is stored separately. |
| **Source URL** | Public page a citation should open. |

Inputs reuse values already present in the store and show their counts. Matching is
case-insensitive, and near matches are highlighted to prevent accidental vocabulary drift while
still allowing genuinely new values.

### Metadata defaults and path rules

Select **Add metadata** during an import to apply defaults to every imported document. Folder
imports can also add **Rules by path**. A rule uses a glob and can either skip matching files or
set one metadata field for them.

Put specific scalar rules before general ones. List fields such as versions and tags accumulate
across matching rules. Imported Markdown frontmatter may also supply supported metadata; source
defaults and rules are resolved first, then frontmatter and source-native values.

The preview reports how many files each rule matched. A zero is usually a sign that the glob does
not describe the source layout you expected.

### Source URL templates

For a folder or ZIP import, Source URL is a template expanded separately for every document.
Clickable variable chips append variables to the field. Unknown variables and unmatched braces
must be corrected before the dialog can be saved.

For `docs/guides/auth.md` with Category root `docs` and category `guides`:

| Variable | Result | Meaning |
| --- | --- | --- |
| `{fullPath}` | `docs/guides/auth.md` | Complete path supplied by the source. |
| `{path}` | `guides/auth.md` | Path with the configured Category root removed. |
| `{pathNoExt}` | `guides/auth` | `{path}` without its extension. |
| `{dir}` | `docs/guides` | Directory portion of `{fullPath}`. |
| `{filename}` | `auth.md` | Filename including extension. |
| `{name}` | `auth` | Filename without extension. |
| `{ext}` | `md` | Extension without a leading dot. |
| `{category}` | `guides` | Final derived category, including any destination prefix. |
| `{title}` | `auth.md` | Source-provided title, or the filename when none is provided. |

Example:

```text
https://docs.example.com/{pathNoExt}
```

becomes `https://docs.example.com/guides/auth`.

To extract part of a variable, use `{variable:/pattern/}`. Its first capture group is used, or the
complete match if the pattern has no capture group. For example:

```text
https://docs.example.com/{name:/^\d{4}-\d{2}-\d{2}_(.+)$/}
```

turns the name `2026-09-04_servicestack-pdf` into the URL slug `servicestack-pdf`. Invalid regexes
are rejected. If a valid regex does not match a document, its Source URL is omitted, a warning is
logged, and the rest of the preview or import continues.

When a variable is appended after another variable, the UI inserts `/` first. Appending `{ext}`
in that position inserts `.` instead. Empty expansions have duplicate slashes normalised without
damaging `https://`.

## Exploring documents

Explorer combines a category browser with search, sorting, and metadata filters.

- **Search** matches document names across the current store view.
- **Categories** opens the category tree with roll-up counts. The number beside a category includes
  documents in its subcategories; the tooltip distinguishes direct and recursive counts.
- Compact filters are available for **doc type**, **status**, **locale**, **product**, **versions**,
  and **tags**. After choosing one, it becomes a removable filter chip and its dropdown disappears
  until cleared.
- **Coverage** provides the complete facet list, missing-value filters, metadata coverage, and
  Gemini sync state.

Active filters are always displayed as chips. Remove one with its `×`, or use **clear all**. Links
from Coverage operate over the whole store and therefore clear the category you were previously
browsing.

Sort by upload date, name, creation time, size, sync issues, failures, or active uploads. Search
results show each document's category so you can jump back into its folder.

Each document row lets you:

- download the cached file;
- open its category;
- edit or add metadata;
- retry its Gemini upload;
- delete it from both the local catalogue and Gemini; or
- start a chat scoped to that single document.

An upload spinner, error icon, active checkmark, sync-state label, or red deleting state provides
feedback for work in progress.
**Queued** means a local document awaits its Gemini upload; **Uploading** means it has started.
Pending, failed, and inconsistent status labels appear before the row's action icons. Healthy
documents use only a green check; its **Local + Gemini** tooltip means the cached file exists and
Gemini reported an active document at the last successful upload or **Sync Store**. **Cache missing**
identifies missing cached files. These indicators reflect the last remote check; **Sync Store**
refreshes them.

**Resume uploads** above the document list resumes queued files in the current folder and its
descendants, or the whole store when browsing its root. A queued row's upload icon resumes that
document. Resuming preserves the queue entries and never re-embeds completed documents.
The status beside the **Sort** dropdown shows running imports, uploads, syncs, deletions,
queued work, and failures for that store. Hover for upload counts; click for details, or click a
**Paused** status to resume the whole store's queued uploads.

Deleting a category removes only that folder and its descendants. After deleting the folder
you are viewing, Explorer returns to the store view so the remaining folders are visible.
The root folder, a selection containing every document, and the last document cannot be
deleted through these actions. To remove everything, delete the File Store and complete its
required confirmation dialog. **Sync Store** reimports files from enabled saved imports, so
documents removed manually can return while they remain in an import's source folder.

## Asking questions over a store

There are three retrieval scopes:

- **New Chat** searches the whole File Store.
- The chat icon on a document searches only that document.
- **Ask about this** preserves the active category and metadata filters from Explorer.

The chat header makes the scope visible. A category is shown as a path, while additional filters
are represented by a count:

```text
docs.servicestack.net/auth (2)
```

Hover over the header to see every filter on a separate line. The same filter expression shown in
**Coverage & filters** is sent to Gemini, so the document view and retrieval query describe the
same selection.

Gemini File Search is a built-in retrieval tool. For a File Search request, the Google provider
sends only `file_search`; other selected function tools are omitted from that request because
Gemini rejects the combination unless server-side tool invocation is enabled. This does not alter
the user's selected tools, and ordinary chats continue to use them.

## Citations and sources

Grounded answers display citation markers beside the claims they support. A **Sources** section is
attached to each individual answer, so earlier messages retain their own sources as the
conversation continues.

Source links are resolved in this order:

1. the document's Source URL;
2. the URI returned by Gemini; or
3. the cached document download.

Set accurate Source URLs when you want readers sent to a public documentation site rather than a
cache file. Source cards can be expanded to inspect the retrieved excerpt.

## Publishing a website Assistant

Open a File Store and select **Assistants** to create a document-grounded chat widget for a
website. Each named Assistant has its own document scope, private behavior prompt, appearance,
hosting rules, public deployment ID, and retained customer conversations.

The Assistant designer includes:

- a visitor-facing title, description, welcome message, and up to six suggested questions;
- optional category, doc type, status, locale, product, version, and tag filters;
- Documentation Guide, Technical Troubleshooter, Customer Support, Developer/API Assistant,
  Product Advisor, Onboarding Guide, and Policy and Procedures prompt templates;
- grounded-answer, citation, response-detail, and fallback-message controls;
- Auto, Light, Dark, Nord, and Matrix color presets, per-color overrides, launcher position, and icon;
- an optional origin allowlist and per-client requests-per-minute limit; and
- a live appearance preview, deployment code, and customer conversation review.

Filters and the system prompt are applied by the server. They are never included in the generated
JavaScript and cannot be overridden by a host page. Publishing marks the File Store as public and
creates a stable deployment URL. A typical embed is:

Each template supplies editable specialist instructions. The server combines them with shared RAG
rules for retrieval, grounding, conflicting documents, prompt-injection resistance, conversation
context, fallback handling, and response formatting. This keeps custom Assistants consistent while
allowing their role and answer strategy to be tailored to the use case.

```html
<script
  src="https://chat.example.com/ext/gemini/public/assistants/widget.js?g=abc123"
  async>
</script>
```

The generated script creates an isolated Shadow DOM component and has no dependency on the host
website's CSS or JavaScript framework. It stores the visitor's session and recent visible messages
in that browser's local storage. The authoritative conversation and every user/assistant message
are retained in the Gemini database so support teams can review recurring questions and missing
documentation later.

Each theme supplies defaults for the widget's `accent-bg`, `panel-bg`, `conversation-bg`,
`assistant-bg`, `user-bg`, `primary-text`, `muted-text`, `link-text`, `error-text`, `warning-text`,
`assistant-text`, `assistant-border`, `user-text`, `user-border`, `panel-border`, and `focus-border`
CSS variables, plus a per-theme `font-family`. The Appearance
editor records independent overrides for Light, Dark, and Nord. Use the reset action beside one
color to return it to that theme's default, or **Reset theme appearance** to restore its complete preset.
Only explicit overrides are saved. Auto has no separate palette: it follows the browser's
`prefers-color-scheme` setting and uses the configured Light or Dark colors, including their saved
overrides. Font-family overrides use normal CSS font-stack syntax and are also stored separately for
each theme.

### Host-controlled appearance

A host page may override only these presentation choices with `data-*` attributes:

```html
<script
  src="https://chat.example.com/ext/gemini/public/assistants/widget.js?g=abc123"
  data-theme="dark"
  data-position="bottom-left"
  data-accent="#7c3aed"
  data-icon="chat"
  async>
</script>
```

Supported themes are `auto`, `light`, `dark`, `nord`, and `matrix`; positions are `bottom-left` and
`bottom-right`; icons are `sparkles`, `chat`, and `help`. `data-open="true"` opens the panel when it
loads. Document scope, prompts, model selection, origin rules, and rate limits cannot be changed by
the embed.

### Restricting host websites

Leave **Allowed origins** empty when an Assistant is intentionally used by many internal or public
sites. Otherwise enter one HTTP(S) origin per line:

```text
https://docs.example.com
https://*.example.com
http://localhost:5173
```

An exact origin includes its scheme and port. A wildcard matches subdomains but not the apex domain,
so add `https://example.com` separately when both are required. The browser can download a public
`<script>` without CORS permission; the security check is applied to every chat request using its
`Origin` header. Requests without an origin are refused when an allowlist is configured.

The public deployment ID is not a secret or authentication credential. Origin rules prevent normal
browser use from unapproved sites, while the rate limit reduces abuse by direct HTTP callers.
Regenerate the deployment ID to invalidate every existing embed immediately.

### Customer conversation retention

Select a saved Assistant and open **Conversations** to review the originating website, page URL,
message count, questions, answers, and citations. Archiving an Assistant or deleting its File Store
disables the public deployment but deliberately retains its conversation history for later review.
Use **Delete permanently** only when the Assistant configuration, public deployment, conversations,
messages, and citations should all be irreversibly removed.

The public endpoint uses `gemini-flash-latest` by default. Set `GEMINI_ASSISTANT_MODEL` in the server
environment to choose a different Gemini model for every published Assistant.

## Editing many documents

Select documents with their checkboxes. You can select the current page or extend the selection to
every document matching the current filters. The selection bar supports **Edit metadata** and
**Delete**.

Bulk metadata operations are intentionally explicit:

- Scalars: **Set where empty**, **Overwrite**, or **Clear**.
- Versions and tags: **Add to list**, **Remove from list**, **Replace list**, or **Clear**.

The dialog reads the selection's existing values and previews how many documents will change, be
kept, or already match. Applying the edit updates the local catalogue only. This staging step lets
you make several corrections and pay for one re-index instead of re-embedding after every edit.

## Pushing metadata changes to Gemini

Coverage reports when local metadata differs from the copy in Gemini. This is not an upload
failure: Explorer already uses the new local values, but filtered chats continue using Gemini's
older metadata until you push.

Open **Coverage**, review the affected documents if needed, then select **Push N to Gemini**. Since
Gemini does not provide an in-place metadata patch, each affected document is re-uploaded and
re-embedded. Progress and an estimated time are shown while the worker runs, and the operation can
be cancelled.

## Coverage and synchronisation

**Coverage & filters** answers two different questions:

- How completely are doc type, status, locale, product, versions, and tags populated?
- Does the local catalogue agree with Gemini?

Click a missing count to open an unscoped Explorer view filtered to documents without that field.
The Gemini sync report can identify:

- documents missing locally;
- documents missing from Gemini;
- missing or mismatched metadata;
- unmatched fields; and
- duplicate remote documents.

Running a sync updates the local state labels and opens Explorer sorted by **Sync Issues** when
problems are found. If duplicate Gemini documents exist, **Prune duplicates** retains one remote
copy, removes the extras, and syncs again.

**Sync Store** reloads and runs every enabled saved import, including refreshing web crawls with
their saved crawl rules and transforms. It discovers source changes and reconciles the local
catalogue with Gemini, queuing uploads and replacements for the background worker.

## Authentication and data scope

When llms.py authentication is enabled, write operations require a signed-in user. Set
`GEMINI_WRITE_ROLE` or `gemini_write_role` in server configuration to require a specific role such
as `Admin`; Administrators always satisfy a configured write role.

Reads remain available in the relevant user/shared scope. File Stores, documents, saved imports,
and the local Gemini database are scoped through the current llms.py user. In a default
single-user installation, data is stored beneath:

```text
~/.llms/user/default/gemini/gemini.sqlite
```

Cached file bodies use content-addressed paths beneath `~/.llms/cache`.

## Troubleshooting

### The Gemini icon is missing

Confirm that `GOOGLE_API_KEY` or `GEMINI_API_KEY` is present in the llms.py server environment, then
restart the server. Gemini is bundled with llms.py and has no additional Python requirements.

### No Gemini model is available

Configure a Google provider chat model. The File Store model picker deliberately excludes models
from other providers and non-chat Gemini models.

### A folder cannot be imported

Check the resolved folder shown by the picker. Non-admin users must choose a path beneath a trusted
or server-allowed directory. Missing directories and paths outside those roots are rejected. The
permission check runs again every time a saved import is executed.

### Files were skipped

Open the import preview's **Skipped & failed** section. Common reasons are an unsupported binary
type in a folder import, fewer than 25 words, an include/exclude glob, a Category root that does not
contain the file, or an explicit skip rule.

### An upload failed or appears stuck

Sort Explorer by **Failed** or **Uploading**. Hover the error icon for the provider message, then
use the re-upload action. The background worker also resumes pending uploads when the extension
starts.

### A metadata filter returns no results

Clear other filter chips and verify the stored spelling in Coverage. Versions and tags are list
fields; each value is filtered independently. Coverage displays the exact Gemini metadata-filter
expression that **Ask about this** will send.

### Citations open cached files

Add or correct Source URL metadata, then push the pending metadata changes to Gemini. Direct links
in existing source cards resolve locally when possible, but Gemini-filtered retrieval sees updated
metadata only after the re-index.

### Explorer and Gemini disagree

Run the sync report. Use its issue links to open a store-wide filtered result, push pending metadata
when the mismatch is intentional, retry missing uploads, and prune duplicates when reported.
