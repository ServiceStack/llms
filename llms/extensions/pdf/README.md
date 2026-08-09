# PDF Studio

Design PDFs with [typst](https://typst.app): edit a `.typ` template and the resources it references in tabs,
with the compiled PDF re-rendering beside them as you type — or describe the change you want and let the
selected model make it.

## Requirements

The `typst` CLI must be on `PATH`. If it isn't, the extension disables itself — no `/pdf` route, no left
sidebar icon, no server routes.

```bash
# one of
cargo install --locked typst-cli
brew install typst
winget install --id Typst.Typst
```

## Templates

Templates live in `~/.llms/user/<user>/pdf` (or `~/.llms/user/default/pdf` when auth is disabled). The folder
is seeded with an `invoice.typ` + `invoice.json` example the first time it's opened.

Every file a template references opens as its own **tab** above the editor — the `.typ` itself, the `.json` it
loads data from, an `#include`d partial, an `image()` (shown as a preview). Tabs are derived from the template
source as you type, so adding `json("prices.json")` makes a `prices.json` tab appear. A referenced file that
doesn't exist yet shows dimmed — open it, type, and **Save** creates it.

The sidecar convention is just the default: **New Template** creates `<name>.typ` alongside `<name>.json`,
which the template reads with typst's `json()`:

```typst
#let data = json("invoice.json")

= #data.invoice.number
```

Because `json()` resolves relative to the `.typ` file, `image("logo.png")` and `include "common.typ"` work the
same way — put shared assets alongside the template. A `fonts/` folder inside the pdf directory is passed to
typst as `--font-path`.

Files that share a name are grouped in the explorer the way IDEs nest build output under its source, and the
row is named after the document rather than one of its files - `invoice`, holding `invoice.typ`,
`invoice.json`, `invoice.ui.json` and any generated `invoice.cs`. The group is collapsed by default (a `+3`
marks what's hidden) and expanding it lets you open each file individually.

Clicking a group opens **the same kind of page you were last looking at**, cascading down until something
exists: a language (`.cs`, `.py`, …) -> the data **Form** -> the data **Code** -> the template. So while
you're reviewing generated C# you can click through documents and stay on C#; hit one without it and you land
on its form instead - and that becomes what the next document opens with.

Right-click anywhere in the file explorer (or use a row's kebab button) for **New Template**, **New File**,
**New Folder**, **Rename**, **Delete** and **Refresh**. New files and folders are created inside whichever
folder was right-clicked; on a group the actions apply to its main file.

## What you start with

The first time you open `/pdf`, the extension copies its bundled starter set into
`~/.llms/user/<user>/pdf` - your own copies, free to edit:

| File | |
| --- | --- |
| `lib.typ` | the shared library: brand tokens, `theme`, `letterhead()`/`wordmark()` with the logo inlined as SVG, `load-data` and the helpers |
| `lib.preview.typ` | one page exercising everything the library styles |
| `invoice.typ` + `invoice.json` + `invoice.ui.json` | a worked example - template, its data, and the schema that drives the Form tab |

They live in [`examples/`](examples/) in the extension, so a `pip install` upgrade never overwrites what you've
changed - the copy only happens into an empty folder (`lib.typ` alone is restored if it goes missing, since
nothing compiles without it).

## lib.typ

Every template imports the shared library that sits at the root of the folder:

```typst
#import "lib.typ": *

#let data = load-data("invoice.json")
#show: theme
```

`theme(doc)` carries the page setup, fonts and `show` rules; `load-data(fallback)` reads `sys.inputs.data`
when the document is rendered with `--input data=<json>` and falls back to the sidecar `.json` while you're
editing, so the same template serves both. Alongside them are tokens (`body-font`, `accent`, `rule-fill`) and
helpers (`money`, `hrule`, `muted`, `title-block`, `field`, `data-table`).

The explorer marks it with a **lib** badge and its own icon so it doesn't read like another document, and
`lib.preview.typ` is grouped under it - a one page document exercising every feature the library styles
(headings, emphasis, lists, links, tables, totals, grids, maths, raw blocks). Change something in `lib.typ`,
open the preview, and the effect is on screen immediately. Any `<name>.preview.typ` groups with its
`<name>.typ` the same way.

`lib.typ` is restored if it goes missing, since templates won't compile without it. New templates created
from the explorer already import it.

## How preview works

Every edit (debounced) POSTs the current *unsaved* buffers — the template plus any resource tab you've edited —
to `/ext/pdf/render`. The server mirrors the templates folder into `~/.llms/cache/pdf/<user>/`, overwrites just
those buffers and compiles with
`typst compile --root <mirror> … -f pdf -`, streaming the PDF bytes back. The browser renders those bytes to a
`<canvas>` per page with the vendored [pdf.js](https://mozilla.github.io/pdf.js/) in `ui/pdfjs/`.

Switching to another template throws the buffers away, so if anything is unsaved you get **Save**,
**Discard** or **Cancel** first - including when the switch comes from the browser's Back button, where
cancelling puts the URL back.

Nothing is written to your real templates until you hit **Save** (or `Ctrl`/`Cmd`+`S`). **PDF** downloads the
exact bytes currently shown in the preview. Compile errors appear above the preview with `file:line:col` and
highlight the offending line in the editor, while the last good render stays on screen; clicking a diagnostic
jumps to it. The preview starts fitted to the panel width and re-fits whenever the splitter moves, until you
zoom manually.

## Edit with AI

The collapsible **Edit with AI** section at the bottom of the editor column sends the template, every text
resource it references and your request to the **currently selected model** — the one in the app's model selector — with the system prompt in
[`prompts/edit-template.md`](prompts/edit-template.md). Models that don't output text (image/audio generation)
are rejected before any request is made.

The model answers with the complete contents of each file it changed, in `path=` tagged code blocks. The
server compiles the result before returning it; if typst rejects it, the diagnostics go back to the model once
for a repair pass, and the better of the two attempts wins.

Drag its top edge to resize it, or click the header to collapse it out of the way - both are remembered.
`↑`/`↓` in the prompt box cycle back through your last 10 prompts (kept in localStorage with the rest of the
panel's state); inside a multi-line prompt the arrows still move between lines as usual.

Edits land as **unsaved buffers** — the tabs go dirty and the preview re-renders, but nothing touches disk
until you Save. **Undo** next to the summary restores the previous contents, and `Ctrl`/`Cmd`+`Z` works per
tab as usual. Because the AI only ever returns file contents, it has no filesystem access and no
`allowed_directories` changes are needed.

### Fixing compile errors

Render failures show above the preview with a **Fix** button that feeds the diagnostics - `file:line:col:
message` for each one - back to the model as a prompt.

The same prompt drives an automatic loop: after an AI edit lands, the designer compiles it and, if typst
rejects it, sends the errors back for another go, up to `MAX_FIX_ATTEMPTS` (3) times. Retries run inside the
original request (past the busy guard), aren't added to the prompt history, and merge their undo snapshots so
one **Undo** reverts the entire chain - including when the loop gives up, where the error keeps its own Undo.
This sits on top of the server's own single repair attempt inside `/ai`.

## Form UI for the data

A `.json` data file gets its own sub-toolbar: **Code | Form** on the left, then a button per language -
**C# | Python | TS | JS** - on the right. The form is generated from a
`<name>.ui.json` **JSON Schema** sitting next to it - press *Generate form schema* and the selected model
writes one from the data ([`prompts/generate-ui-schema.md`](prompts/generate-ui-schema.md)), giving each field
a human title, help text, the right widget (`enum` -> select, `format: textarea`, numbers with
`minimum`/`multipleOf`) and `x-titleKey` so array rows are labelled by a meaningful property.

The form is the shared [`JsonSchemaForm`](../../ui/components/README.md) component - nested objects become
collapsible groups, arrays become rows you can add to and remove, and it also powers the JSON tab on the
`/code` page. The schema endpoint lives in `core_tools` (`/ext/core_tools/schema`) since it isn't
typst-specific.

A template that hasn't got a schema gets one generated automatically after an AI edit compiles (quietly - a
failure there isn't worth interrupting for), and **Regenerate form** in the Form tab rebuilds it from the
current data after the shape changes.

Edits in the form write straight back into the `.json` buffer, so the PDF re-renders as you type, and the
schema is just another file: it opens as a tab, you can hand-edit it to relabel or reorder fields, and it
isn't written to disk until you Save.

## Formatting toolbar

A `.typ` tab gets a formatting bar. Each button works on the selection, or inserts a placeholder and selects
it so you can type straight over it:

| | |
| --- | --- |
| `B` `I` `U` `S` `` ` `` | `*bold*`, `_italic_`, `#underline[...]`, `#strike[...]`, `` `raw` `` |
| `H1` `H2` `•` `1.` | line prefixes (`=`, `==`, `-`, `+`) applied to every selected line - click again to remove them |
| link, table, centre, rule, page break | block snippets: `#link`, a `#table` with a header row, `#align(center)`, `#line`, `#pagebreak()` |
| image | uploads a picture (or picks one already in the folder) and inserts its `#image(...)` |
| `T` | the text and font picker below |
| page | the page setup dialog below |

### Fonts

The `T` button opens a picker listing every family `typst fonts` can see - the system fonts plus anything in
your `fonts/` folder, which is also passed to the compiler with `--font-path`. The list comes from
`GET /ext/pdf/fonts` and is cached on that folder's mtime, so a font you drop in shows up without a restart.

Filter by name, set the size, colour, weight and letter spacing, and each family previews in its own face.
**Apply Style** then either wraps the selection in `#text(font: "...", ...)`, or - with nothing selected -
sets it for the whole document: it rewrites the existing `#set text(...)` rule if there is one, otherwise
inserts one at the top, since a `#set` rule only styles what comes after it.

### Page setup

The page icon opens a dialog for `#set page(...)`: paper size (A3-A6, US Letter/Legal/Tabloid, 16:9 and 4:3
slides, or a custom mm size), portrait/landscape, margin, columns, page numbering and a background fill, with
a proportional preview of the resulting page and its margin box.

It **merges** rather than rewrites: options the dialog doesn't manage - a `header:`, a
`margin: (x: 2cm, y: 1.5cm)` - are left exactly as they were, and a blank margin field leaves the existing one
alone. Like the font picker it edits the document's existing `#set page` rule when there is one, otherwise it
inserts one at the top.

### Markdown

The **Markdown** button renders Markdown inside the template with the
[cmarker](https://typst.app/universe/package/cmarker) package - useful when the content is already Markdown
(a model's answer, a README, notes) and you only want typst for the page furniture:

```typst
#import "@preview/cmarker:0.1.10"

#cmarker.render(```md
# Heading

Markdown with **bold**, _italic_ and a list:

- one
- two
```)
```

What the button does depends on the selection:

| Selected | Result |
| --- | --- |
| nothing | the starter block above, at the cursor |
| a reference - `#data.body`, `data.at("body")`, `read("notes.md")` | `#cmarker.render(data.body)` - the `#` goes, since inside the call you're already in code |
| anything else | wrapped as literal Markdown in a ` ```md ` block |

A leading `#` is typst's own marker for code, so `#notes` is treated as a reference; without one a bare word
like `Introduction` is treated as Markdown, and a `.field` or a `(call)` is what marks it as an expression.

That reference form is the useful one for data-driven templates: keep the prose in the `.json` as Markdown and
let cmarker lay it out. The button adds the `#import` at the top of the file if it isn't already there - once
per file, however many blocks you insert. typst downloads `@preview`
packages into its own cache the first time one is compiled, so the very first Markdown render needs network
access; after that it works offline.

## Saving rendered PDFs

**Save** next to the **PDF** download button writes the *same bytes the preview is showing* to
`saved/<template>/`, so a template's output is versioned next to it rather than lost in a downloads folder.
The filename is pre-filled as `<template>-0001.pdf`, numbered one past the highest already in that folder, and
you can edit it before saving.

Because `saved/` lives inside the templates folder it shows up in the explorer like anything else - clicking a
`.pdf` there opens it in a new tab through the browser's own viewer rather than trying to load it into the
editor.

| Method | Route | Description |
| --- | --- | --- |
| POST | `/ext/pdf/pdf?path=` | raw `application/pdf` body, written to that path (must be under the templates folder, `%PDF` magic checked, 50MB cap) |
| POST | `/ext/pdf/asset?path=` | raw image body - png/jpg/gif/svg/webp, magic checked, 20MB cap, 409 if it exists |

## Deep links

The open template is in the URL - `/pdf?template=invoice.typ` - so reloading comes back to the same document
and the browser's back/forward buttons walk through the templates you've opened. The URL is the source of
truth on load; if it names a template that no longer exists, the designer falls back to the first one and
rewrites it. Picking a template from the explorer pushes a history entry; moves you didn't navigate to (the
first template on load, a rename, a delete) replace it instead, so back doesn't retrace them.

## Build a template from a screenshot or PDF

The paperclip in **Edit with AI** attaches images the model builds from - drop them on the panel or paste a
screenshot straight into the prompt box. Attach a design, a PDF you want to reproduce, or a screenshot of the
document you're replacing, then ask for it to be rebuilt as a template.

PDFs are rasterised in the browser with the same vendored pdf.js the preview uses (first 4 pages), so this
needs nothing from the provider beyond image input - no PDF-specific API. Images are downscaled to 1568px on
their longest edge before upload; up to 8 attachments per request.

The prompt tells the model to treat the image as the target output and to **split what it sees**: layout,
labels and styling become the `.typ`, while everything that reads as data - names, dates, references, line
items, totals - is transcribed into the `.json` sidecar. So the result is a reusable template, not a
hardcoded copy of your screenshot.

Attachments need a vision model. If the selected model can't read images the designer says so before sending
anything, and the server refuses the request too (the check is `modalities.input`, so a model we don't know
is left to the provider).

## Generate typed classes

The language buttons in the `.json` sub-toolbar turn the data file into typed classes so application code can
produce the JSON the template expects.

Generation is **deterministic and local** using `@servicestack/vue`'s `generateTypes()` - no model, no request,
no waiting, and the same input always produces the same output. A JSON example only carries JSON's six
types, so **generate the form schema first** if you want richer types: `.ui.json` turns `required` into
non-nullable members, `multipleOf: 0.01` into `decimal`, `format` into date/uuid types, `enum` into a real enum
and `description` into doc comments. Where the schema and the example disagree - a `format: date` against a
`"31 July 2026"` - the example wins, so the generated types can still parse the data file sitting next to them.

| Language | Output |
| --- | --- |
| C# | `System.Text.Json` classes with `[JsonPropertyName]`, `List<T>`, nullable-clean initialisers |
| Python | `@dataclass_json` + `@dataclass` with `typing` annotations and `config(field_name=...)` key mapping |
| TypeScript | exported classes with `constructor(init?: Partial<T>)`, strict-mode clean |
| JavaScript | the same classes with JSDoc `@type` annotations |

Structurally identical objects collapse into one type, recursive schemas generate recursive classes, and keys
that aren't identifiers (`X-Api-Key`, `2fa`) keep their wire name through `[JsonPropertyName]` /
`config(field_name=...)` / a quoted TS property.

The generated code shows **in place** - the `.json` tab stays open with the language button active - so you can
click between `Code`, `Form` and each language without leaving the document.

Because generating costs nothing, nothing is stored: every click regenerates from the current data, and the
view updates as you edit the `.json` or its form. It's **read-only** and never written to disk - there's no
dirty marker and **Save** ignores it. Use **Copy** to take it into your project.

## Syntax highlighting

The app's bundled highlight.js is the 35-language "common" build, which has no typst grammar - so ` ```typst `
blocks (which the AI features produce constantly) rendered as plaintext in chat. The extension registers one
from [`ui/typst-hljs.mjs`](ui/typst-hljs.mjs) on install, covering comments, `#` code expressions, keywords,
built-ins, strings, lengths (`2cm`, `1fr`, `50%`), headings, raw, math, labels and refs. The editor uses a
separate CodeMirror grammar in [`ui/typst-mode.mjs`](ui/typst-mode.mjs).

Because it registers on install, typst highlighting in chat follows the extension - if `typst` isn't on `PATH`
the extension is disabled and blocks fall back to plaintext. Move `registerTypst()` into `llms/ui/markdown.mjs`
if you'd rather have it unconditionally.

The `/code` page's **JSON** tab has the same sub-toolbar - `Code | Form` plus `Schema | C# | Python | TS | JS` -
over a single document kept in localStorage, using the same shared endpoints and form component.

## Routes

| Method | Route | Description |
| --- | --- | --- |
| GET | `/ext/pdf/files` | file tree of the templates folder |
| GET | `/ext/pdf/file?path=` | read a text file (404 when it doesn't exist yet) |
| GET | `/ext/pdf/raw?path=` | the file as-is, used to preview referenced images |
| POST | `/ext/pdf/render` | `{path, files}` — compile with unsaved buffers overlaid → `application/pdf` |
| POST | `/ext/pdf/file` | save `{path, content}` |
| POST | `/ext/pdf/create` | new template `{path, withData}` |
| POST | `/ext/pdf/folder` | new folder `{path}` |
| POST | `/ext/pdf/rename` | rename `{from, to}` — moves the whole group and fixes references |
| DELETE | `/ext/pdf/file?path=&sidecar=` | delete a file or folder |
| POST | `/ext/pdf/ai` | `{path, prompt, model, files, images}` — AI edit, returns the changed file contents |
| POST | `/ext/core_tools/schema` | `{name, model, content}` — JSON → a form schema (shared) |

All paths are relative to the user's pdf folder and validated against escaping it.
