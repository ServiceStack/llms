# PDF Designer

Design PDFs the way you write code: a plain-text template on the left, the finished page on the right,
updating as you type.

![The PDF Designer with a template open and its live preview](./docs/designer-overview.png)
<!-- screenshot: the full /pdf page - explorer, invoice.typ in the editor, rendered invoice in the preview -->

Documents like invoices, certificates, reports and statements are usually trapped in a Word file someone has
to open and edit by hand. Here the **layout** is a [typst](https://typst.app) template and the **content** is
a JSON file, so the same design can be filled with different data - one customer or ten thousand - and
rendered to a pixel-identical PDF every time.

## Why you might want this

- **Your documents become source code.** Templates are text: diff them, review them, keep them in git.
- **Content is separate from design.** Change the wording of an invoice without touching its layout, or
  restyle every document at once by editing one shared library file.
- **See it while you write it.** Every keystroke re-renders the real PDF - not an approximation of it.
- **Describe changes in English.** Attach a screenshot of a document you want to reproduce and let the AI
  build the template for you.
- **Generate the data from your app.** The designer writes matching C#, Python, TypeScript or JavaScript
  classes, so your code produces exactly the JSON the template expects.

## Before you start

The designer needs the [typst](https://github.com/typst/typst) compiler on your `PATH`. Install it with
`cargo install typst-cli`, `brew install typst`, `winget install typst`, or grab a binary from its releases
page. Without it the **PDF Designer** icon doesn't appear at all.

## Your first visit

Open the PDF icon in the left toolbar. The first time you do, you get your own copy of a starter set in
`~/.llms/user/<you>/pdf`:

| File | What it is |
| --- | --- |
| `invoice.typ` | a worked invoice template |
| `invoice.json` | the data it renders |
| `invoice.ui.json` | a schema, so the data can be edited as a form |
| `lib.typ` | shared styles every template imports |
| `lib.preview.typ` | one page showing everything `lib.typ` styles |

They're yours - edit them freely. Upgrading the app never overwrites your changes.

## The workspace

![The three panes: explorer, editor, preview](./docs/designer-panes.png)
<!-- screenshot: annotate the three columns - explorer / editor+toolbar / preview -->

**Explorer** (left) lists your templates. Related files are grouped: `invoice` holds `invoice.typ`, its
`.json` data and its `.ui.json` schema, with a `+2` badge showing how many are tucked inside. Click a group to
open the document; click the chevron to see its parts. Right-click anywhere for **New Template**, **New
File**, **New Folder**, **Rename** and **Delete**.

**Editor** (middle) is the template, with tabs across the top for every file the document uses.

**Preview** (right) is the compiled PDF. Zoom with `−`/`+`, or **Fit** to size it to the panel; it re-fits
when you drag the splitter.

Edits are held in memory until you press **Save** (or `Ctrl`/`Cmd`+`S`) - a dot on the tab marks unsaved work.
The preview always shows your unsaved state, so you can experiment freely and walk away without saving.

## Editing the data

A template's data lives in the `.json` file beside it. Open that tab and you get two ways to edit it:

![The Form view of a template's data](./docs/data-form.png)
<!-- screenshot: invoice.json with the Form toggle active, showing grouped fields and an array of line items -->

- **Code** - the raw JSON.
- **Form** - a real form with labelled fields, collapsible sections and add/remove buttons for lists.

Either way the preview re-renders as you go. The Form is built from the `.ui.json` schema; if a template
doesn't have one, press **Schema** and a model writes it for you - the only part of the data workflow that
needs a model selected.

## Generating code for your app

The language buttons turn the data file into typed classes, so the JSON your application produces always
matches what the template expects.

![C# classes generated from the invoice data](./docs/generated-types.png)
<!-- screenshot: the .json tab with the C# button active, generated classes in the editor, Copy button visible -->

| | |
| --- | --- |
| **C#** | `System.Text.Json` classes with `[JsonPropertyName]` |
| **Python** | `@dataclass_json` dataclasses |
| **TS** / **JS** | exported classes with a `Partial<T>` constructor |

It's instant and needs no model, so nothing is stored - each click regenerates from the current data and the
view is read-only. Use **Copy** to take it into your project. Generate the schema first if you want richer
output: it's what turns `required` into non-nullable members, `multipleOf: 0.01` into `decimal`, dates into
`DateTime` and `enum` into a real enum.

## The formatting toolbar

![The formatting toolbar above a template](./docs/toolbar.png)
<!-- screenshot: the toolbar row above the editor, with the buttons visible -->

Working on a `.typ` file gives you a toolbar for the markup you'd otherwise memorise:

- **B** *I* U S and `` ` `` wrap the selected text.
- **H1 H2 • 1.** prefix the selected lines - click again to remove them.
- Link, image, table, centre, rule and page-break drop in ready-made blocks.
- **T** opens the text and font picker.
- The page icon opens page setup.
- **Markdown** renders Markdown inside the page.

### Fonts and text

![The text and font picker](./docs/font-picker.png)
<!-- screenshot: the Text & Font Formatting dialog, filtered font list with a live sample -->

**T** lists every font typst can see - your system fonts, plus anything you drop in a `fonts/` folder next to
your templates. Filter by name, set the size, colour, weight and letter spacing, and each family previews in
its own face. **Apply Style** styles the selected text, or the whole document when nothing is selected.

### Page setup

![The page setup dialog](./docs/page-setup.png)
<!-- screenshot: the Page Setup dialog showing paper, layout, margin, columns, numbering and the page preview -->

The page icon covers paper size (A3-A6, US Letter/Legal/Tabloid, slides, or a custom size), portrait or
landscape, margins, columns, page numbering and a background fill, with a preview of the page shape. It edits
around anything it doesn't manage, so a custom header or margin you wrote by hand is left alone.

### Markdown

If your content is already Markdown - notes, a model's answer, a README - the **Markdown** button renders it
in place, so typst only handles the page furniture. Select a reference like `#data.body` before clicking and
it wraps that instead, which is the useful form: keep the prose in your `.json` as Markdown and let the
template lay it out.

## Editing with AI

![The Edit with AI panel](./docs/edit-with-ai.png)
<!-- screenshot: the Edit with AI panel expanded, a prompt typed, and the Updated file chips after a run -->

Expand **Edit with AI** at the bottom of the editor, describe what you want - *"add a Due column and highlight
overdue invoices in red"* - and the selected model rewrites the template and its data together. Changes land
as unsaved edits you can read, re-render and **Undo** in one click before saving. `↑`/`↓` cycles your last ten
prompts.

If the model writes something typst rejects, the designer hands the error back and gives it one shot at fixing
its own work before showing you the result.

### Building a template from a screenshot or PDF

![Attaching a screenshot to the AI panel](./docs/ai-attachment.png)
<!-- screenshot: the AI panel with a thumbnail attached and the pre-filled "Rebuild ..." prompt -->

Attach with the paperclip, drag a file onto the panel, or paste a screenshot straight into the prompt box -
PDFs come in as page images (first 4 pages). Have a design you want to reproduce, or a document you're
replacing? Attach it and the prompt fills itself in.

The model is asked to **split what it sees**: layout, labels and styling become the template, while names,
dates, references and line items are transcribed into the `.json`. You get a reusable template, not a
hardcoded copy of your screenshot. Attachments need a model that can read images.

## The shared library

Every template starts with the same three lines:

```typst
#import "lib.typ": *

#let data = load-data("invoice.json")
#show: theme
```

`lib.typ` is where your house style lives - fonts, colours, the page setup, your logo and letterhead, and
helpers like `money()` and `title-block()`. Change it once and every document follows.

![lib.preview.typ showing the library's styles](./docs/lib-preview.png)
<!-- screenshot: the lib group expanded in the explorer with lib.preview.typ open and rendered -->

The explorer marks it with a **lib** badge, and `lib.preview.typ` is grouped underneath: one page exercising
every feature the library styles. Edit `lib.typ`, open the preview, and you can see the effect on headings,
tables, totals, links and rules at once.

`load-data` is what lets the same template serve both the designer and your application: while you're editing
it reads the `.json` beside it, and in production it takes data passed straight in
(`typst compile --input data='{...}' invoice.typ out.pdf`).

## Getting the PDF out

- **PDF** downloads the rendered file.
- **Save** keeps a copy in `saved/<template>/`, numbered `invoice-0001.pdf`, `invoice-0002.pdf` and so on -
  the same bytes the preview is showing.

![Saved PDFs in the explorer](./docs/saved-pdfs.png)
<!-- screenshot: the saved/ folder expanded in the explorer showing numbered PDFs -->

Saved PDFs appear in the explorer like any other file; click one to open it in a new tab.

## Handy to know

- **Deep links.** The open template is in the URL (`/pdf?template=invoice.typ`), so a reload comes back to
  where you were and the browser's back and forward buttons walk through the documents you've opened.
- **Unsaved work is safe.** Switching to another template with unsaved changes asks you first: **Save**,
  **Discard** or **Cancel**.
- **Renaming keeps everything wired up.** Renaming a template renames its data and schema with it, rewrites
  the references inside it, and fixes the `#import` in any other template that used it - so renaming
  `lib.typ` doesn't break every document that imports it. Unsaved changes are written out first.
- **Errors point at the line.** A typst error shows under the toolbar with its file and line number - click it
  to jump there. The last good preview stays on screen while you fix it.
- **Images and assets.** Drop them anywhere in your templates folder and reference them relatively, e.g.
  `#image("logo.png", width: 40%)`.
- **Fonts.** A `fonts/` folder in your templates directory is passed to the compiler, so you can ship brand
  fonts alongside the templates that use them.

## Keyboard shortcuts

| | |
| --- | --- |
| `Ctrl`/`Cmd` + `S` | save every unsaved file |
| `Enter` in the AI box | send the prompt |
| `↑` / `↓` in the AI box | cycle previous prompts |
| `Esc` | close a dialog or menu |

## Learning typst

The templates are ordinary [typst](https://typst.app/docs) documents - its
[tutorial](https://typst.app/docs/tutorial/) is short and worth the half hour. The quickest way to learn here
is to open `lib.preview.typ`, change something, and watch what happens.
