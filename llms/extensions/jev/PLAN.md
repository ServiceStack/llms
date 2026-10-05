# Jev extension: Decision Studio implementation plan

Status: implemented and verified; see the delivery notes below.
Created: 2026-10-01.

## 1. Product objective

Build a polished, approachable interface for making structured decisions with Jev through OpenRouter.
The extension directory and API namespace are `jev`; the user-facing page is **Decision Studio**, with
**Jev via OpenRouter** as its engine label. Add a decision tree icon labelled **Decision Studio** to
the application's left icon bar and a dedicated `/jev` route.

The primary workflow is:

**Choose a recipe → provide input → run a decision → understand the results → refine and reuse.**

A person should be able to try a useful recipe without learning the API, editing JSON, or writing a
prompt. A more experienced user should be able to create, test, export, and reuse a recipe without
giving up control over the underlying request.

### UX acceptance goals

- Opening the page immediately exposes one editable Message sentiment recipe and a clear next action.
- A first decision requires selecting a recipe, entering its required fields, and pressing **Run**.
- Every field and result has a meaningful label; API names appear only where they help advanced users.
- One primary action is visually prominent in each context. Secondary tools stay quieter.
- The library, input form, and results are easy to distinguish without excessive borders or dashboards.
- Loading, empty, error, unsaved, conflict, and interrupted states are designed explicitly.
- Light and dark themes, keyboard navigation, and narrow screens receive the same level of attention
  as the desktop happy path.
- Recipe edits and async responses never overwrite another recipe's draft or results.
- The UI displays uncertainty accurately, without presenting confidence as measured accuracy.

## 2. Scope of the first complete release

Include:

1. A lazy-loaded extension page and left-bar entry.
2. A searchable library of editable personal recipes and favourites, plus a bundled recipe import collection.
3. Schema-driven input forms, explicit example loading, and per-recipe draft recovery.
4. Visual editing of recipe fields, questions, options, and ordered scales.
5. A synchronized advanced recipe JSON editor and a read-only compiled request preview.
6. Server-side OpenRouter Decisions integration, using existing provider credentials.
7. Readable answer cards for Choice, Score, and Noul, with distributions and call details.
8. **Create with AI** and **Improve with AI**, using a separately selected text-generation model.
9. Per-user recipe storage, immutable run snapshots, and local run history.
10. Saved example cases, expected-answer annotations, and small sequential example checks.
11. Recipe import/export, response export, and copyable request/curl examples without secrets.
12. Functional verification and visual review of representative states.

Defer bulk CSV/JSONL jobs, sophisticated calibration reports, automatic external actions, multi-stage
workflow graphs, direct TypeSafe hosting, and integration into the durable chat agent scheduler. These
can build on the recipe and run contracts after the interactive experience is complete.

## 3. Existing code to reuse and architectural boundaries

| Concern | Existing reference | Implementation decision |
|---|---|---|
| Left icon and lazy route | `../pdf/ui/index.mjs`, `../core_tools/ui/index.mjs` | Register with `ctx.setLeftIcons`; load page modules on navigation |
| Extension-scoped requests | `../../ui/ctx.mjs`, `ExtensionScope` | Use `ctx.scope('jev')`; keep requests under `/ext/jev` |
| Schema-driven forms | `../core_tools/ui/pages.mjs`, `../pdf/ui/pages.mjs` | Reuse the globally installed `JsonSchemaForm` after checking supported widgets |
| Editor loading | `../../ui/lazy.mjs` | Load CodeMirror only when opening an advanced editor |
| AI generation | `../core_tools/__init__.py`, `../pdf/__init__.py` | Use `ctx.chat_completion` with tools/history/storage disabled |
| Provider key resolution | `../../main.py`, `create_provider_kwargs`, `get_registered_provider` | Resolve the configured OpenRouter provider on the server |
| Authentication | `ExtensionContext.assert_username` and extension request wrapper | Resolve ownership from the authenticated request |
| Persistence conventions | `../../db.py`, `../app/db.py` | Follow per-user ownership conventions; Jev uses JSON files |
| Styling | `$styles`, shared Vue controls, existing extension pages | Follow application theme and control conventions |
| Async ownership | `../../../docs/CHAT_THREADS.md` | Apply origin-snapshot rules to Jev drafts and runs |

Keep the implementation predominantly inside `llms/extensions/jev/`. Do not send decision requests
through Chat Completions, register Jev as a normal chat model, or persist decisions as canonical chat
messages. The AI recipe author uses Chat Completions; Jev decision execution uses the separate endpoint.

Use Python standard library plus `aiohttp`, native Vue 3 ESM, and existing vendored frontend libraries.
Do not add a bundler, a new SDK, a general-purpose schema dependency, or a charting dependency for simple
probability bars. Any small shared UI change must preserve existing callers and be justified by actual
reuse. The extension must work when PDF Studio or core_tools is disabled.

Existing unrelated workspace changes must be preserved. Verify package asset inclusion for prompts,
recipe JSON, styles, and ESM modules when completing the implementation.

## 4. Information architecture and visual design

### Page shell

- Use the existing application chrome, with the Jev icon's active state following `/jev` routes.
- Within the page, use a compact recipe sidebar and a main workspace.
- Header: page identity, selected decision model, connection status, and **Create with AI**.
- Recipe sidebar: search, **All / Favourites / My recipes**, compact rows, and a **New recipe** action.
- Workspace header: recipe title, short purpose, personal/draft marker, revision/save state, and a
  restrained overflow menu for duplicate, favourite, export, and delete.
- Default workspace: input form on the left, current result on the right.
- Workspace views: **Run**, **Edit**, **Examples**, and **History**. Keep JSON inside the relevant view
  as an advanced option instead of making it a prerequisite for normal use.
- On narrow screens, collapse the library into a recipe picker/drawer and stack inputs above results.
  Keep Run easy to reach without introducing overlapping sticky controls.
- A history detail view is a clearly labelled snapshot, with **Use these inputs** and **Run again**;
  it must not masquerade as the current editable recipe.

### Polish requirements

- Reuse application colours and `$styles`; scope extension-specific CSS to the page root.
- Use a consistent spacing scale, restrained rounding, readable type, and a clear title/body/help
  hierarchy. Use monospaced text only for keys and JSON.
- Use semantic buttons, explicit input labels, visible focus states, and accessible dialog patterns.
- Avoid hover-only actions, colour-only meaning, and drag-only reordering. Provide move-up/down controls.
- Use inline field errors, compact page banners for connection failures, and toasts for simple successes.
- Make slow operations visible immediately; avoid fake progress percentages. Skeletons should retain
  layout and never obscure editable input unnecessarily.
- Respect reduced motion. Any result transition should be brief and functional.
- Preserve primary actions and readable content at 360px, 768px, 1280px, and wide desktop sizes; also
  verify enlarged text and browser zoom.

### First-use and connection states

The library and recipe editor remain usable without an OpenRouter key. Show an actionable connection
notice and a link to the existing provider settings, explaining that Jev uses the OpenRouter account.
Do not create a second API-key field or expose secrets to the browser. An empty personal library offers
**Start from a recipe**, **Create with AI**, and **Create manually**.

Selecting a starter shows its blank/default form and an explicit **Try example** action. Examples never
silently become the user's real input. Running remains disabled until required input is valid, with a
visible reason and field-level feedback.

## 5. Recipe contract

Use a versioned JSON document with server-owned identity and revision stored separately from its
portable content. Keep form data, Jev questions, and presentation metadata distinct.

Illustrative portable recipe:

```json
{
  "schemaVersion": 1,
  "name": "Message sentiment",
  "description": "Assess the overall tone of an email, tweet or comment.",
  "tags": ["sentiment", "email"],
  "decisionModel": "~typesafe/jev-latest",
  "inputSchema": {
    "type": "object",
    "properties": {
      "message": {"type": "string", "title": "Email, tweet or comment", "format": "textarea"}
    },
    "required": ["message"],
    "additionalProperties": false
  },
  "state": {"mode": "object"},
  "questions": {
    "sentiment": {
      "type": "choice",
      "instructions": "What overall sentiment does `message` express?",
      "criteria": {
        "negative": "An unfavorable attitude.",
        "neutral": "No favorable or unfavorable attitude.",
        "positive": "A favorable attitude.",
        "mixed": "Both favorable and unfavorable attitudes."
      }
    }
  },
  "presentation": {
    "questions": {
      "sentiment": {
        "label": "Sentiment",
        "optionLabels": {"negative": "Negative", "neutral": "Neutral", "positive": "Positive", "mixed": "Mixed"}
      }
    }
  },
  "examples": []
}
```

### State construction

For version 1, prefer identity mapping: the validated form object becomes `state`. Support a second
`text` mode with an explicit source field for simple pasted-text recipes. Structured input eliminates
the need for a miniature template language. Use literal field paths in question instructions to identify
relevant input. Add richer mapping only when a real recipe requires it.

Build Python/JavaScript objects first and serialize normally. Never interpolate input into raw JSON,
evaluate expressions, or let a recipe supply executable code or an arbitrary upstream URL.

### Supported input-schema subset

Start with object roots, strings, numbers, integers, booleans, enums, simple nested objects, and arrays
of primitive values. Support titles, descriptions, defaults, required fields, string length, numeric
bounds, array bounds, and `additionalProperties`. Use the existing renderer's `format: textarea/date/email`
conventions. Keep a supported-keyword list and reject unsupported validation constructs explicitly.
Do not claim full JSON Schema compliance. Treat declared date/email formats consistently between UI and
server, documenting whether each is a widget hint or an enforced constraint.

Cap nesting, fields, options, examples, and serialized size using documented extension limits. Reconfirm
the upstream question/criteria limits before choosing those constants. Reject remote `$ref`, executable
UI strings, unsupported union/composition keywords, non-finite numbers, and invalid enum/default values.
Provide errors with paths such as `questions.sentiment.criteria` rather than generic "invalid JSON".

Share fixture-based contract cases between Python and JS validators. The backend is authoritative;
the frontend validator improves immediate feedback. Keep schema migration explicit and reject unknown
future `schemaVersion` values without rewriting the imported file.

### Question and presentation invariants

- A recipe has at least one question. Output keys are stable, unique, and validated.
- Choice criteria are named alternatives; Score criteria retain their exact order; Noul has separate
  true/false descriptions when criteria are supplied.
- Keep labels outside the provider payload. Compilation emits only supported Jev request fields.
- V1 visual editing handles text instructions/criteria; structured advanced values supported by the
  upstream contract must either be preserved in JSON-only mode or rejected explicitly. Never stringify
  or discard them silently when changing views.
- Renaming/removing an option or question also updates its presentation references. Existing history
  retains the old snapshot; affected expected-answer annotations are flagged for review.
- Recipe linting suggests focused questions, disjoint Choice options, meaningful scale levels, and an
  "unclear" option where appropriate. Heuristic warnings are advisory, distinguishable from hard errors.

## 6. Editing, drafts, and library behaviour

Every library recipe is an editable user-owned JSON file. Initialize a new user with only a personal
copy of Message sentiment. Keep the seven bundled JSON recipes in the package as an import collection,
separate from the library. **Import recipe** opens a centered searchable modal listing names,
descriptions, tags, input counts, and question counts. Import writes an editable personal copy. Recipe
filenames identify copies uniquely. Importing an existing filename requires a warning and confirmation before
replacing the definition and clearing its history. Deleting the initial recipe never silently recreates it. **Import from
file** validates and saves an owned copy directly. Duplication remains an optional explicit user action.

Personal recipes use explicit **Save** with a visible saved/unsaved indicator. Form input is a separate
draft, so typing an article does not create a new recipe revision. Recover both input drafts and unsaved
recipe edits in IndexedDB, namespaced by authenticated account, server base, and recipe/local draft ID.
Only non-sensitive display preferences may use the standard extension preference namespace.

Keep structured recipe data as the accepted editor state. A raw JSON buffer can be temporarily invalid:
show its diagnostics and retain the last valid structured value. **Apply JSON** validates and replaces
the structured state atomically. Do not execute a stale structured recipe while the visible JSON buffer
contains unapplied edits. Clarify the distinction between **Recipe JSON** (editable) and **Request JSON**
(compiled preview/copy).

Validate imported recipes before saving. Both import paths use the JSON filename stem, excluding the final
`.json` extension, as the ID. Reject unsafe or reserved filenames before constructing paths. Replacement verifies the
confirmed revision, clears only matching history, and requires active decisions to be stopped first.
The import collection previews each recipe’s purpose and input/question counts. Uploaded filenames
must be single portable JSON filenames; embedded IDs never select a storage path. Export portable recipe content
and examples, without ownership fields, credentials, transient draft values, or run history.

Protect concurrent edits with optimistic revision checks. A 409 offers reload or **Save as a copy**,
retaining the local edit. Do not silently overwrite the server's current revision. Deletion uses the
application's small inline confirmation convention and explains that recorded run snapshots remain.

## 7. Starter recipes and content quality

Ship a curated initial set with clear descriptions, useful examples, and focused criteria:

| Recipe | Input | Decisions |
|---|---|---|
| Message sentiment | Email, tweet or comment | Sentiment; urgency as a separate signal |
| Support triage | Ticket, customer tier | Bug report; owning team; urgency |
| Company news | Company/ticker, article | Company relevance; business impact; materiality |
| Email intent | Email text | Primary intent; response requested; time-sensitive request |
| Feedback tags | Feedback text | Independent feature/bug/usability/praise checks; severity |
| Audience relevance | Content, audience description | Relevance score; promotional intent |
| Claim support | Claim, source text | Supported/contradicted/insufficient evidence |

Each starter should demonstrate a useful pattern rather than pad the library. Provide a few contrasting
examples, including ambiguous or incomplete input. Clearly label authored/AI-suggested expected values;
only user-reviewed labels count as verified expectations. Examples are illustrative, not benchmark claims.

The company-news recipe assesses reported business implications, with mixed/unclear outcomes. Do not
present its output as a stock-price forecast. The sentiment recipe assesses the overall message tone without a subject field.
Independent binary questions enable multiple feedback tags; Choice is for a single selected category.

## 8. Server adapter and request execution

### Provider contract

Use `POST https://openrouter.ai/api/alpha/decisions` with `model`, `state`, and `questions`. Resolve the
configured OpenRouter provider and its server-side key using existing provider resolution conventions;
respect configured headers and enabled/disabled status. Recheck the provider's actual attributes before
implementation. If a narrow resolver helper is needed, keep it local unless a shared change is necessary.

Expose an explicit decision model list, starting with `~typesafe/jev-latest` and supported pinned Jev
releases verified against current docs/catalogue. Restrict execution to supported decision models;
keep their selector separate from the global chat selection. Do not send them to `/chat/completions`.
Record the requested model and actual response model. Prices/context values must not be permanent UI
claims; show returned usage/cost and available current metadata.

Use one lazy `aiohttp.ClientSession` per application lifecycle, with cleanup registered through the
extension context. Apply explicit request timeouts and bounded response sizes. Do not pass through arbitrary
user-supplied headers or provider endpoints. Never log Authorization, credentials, or complete input bodies.

### Run ownership and lifecycle

Create an immutable run record from the validated recipe draft and input before dispatching upstream.
Execution is permitted from an unsaved recipe: its snapshot is authoritative even when no persistent
recipe ID exists. The server supplies run identity, timestamps, ownership, compiled request, and hash.

Return an accepted run ID promptly; execute the short API call in an extension-owned background task.
This allows the user to change pages or recover the result after a browser reload. Keep a bounded active
task registry and a small per-user concurrency limit; reject overload with a useful retry message instead
of building an unbounded queue. Tasks do not use chat scheduling or tool execution.

Persist states `pending`, `running`, `succeeded`, `failed`, `cancelled`, and `interrupted`. Task handlers
must consume exceptions and commit a terminal result. Use a bounded execution lease/deadline with an
owner token; reconcile expired nonterminal records on history/status reads or startup. This prevents
restart survivors remaining "Running" indefinitely and avoids one worker interrupting another worker's
live run. An interrupted request is never automatically submitted again.

Use a client-generated `submissionId` scoped to the user. A repeated submission with the same snapshot
returns its existing run; reuse with different input returns 409. A deliberate **Run again** generates
a new submission ID. Idempotency prevents duplicate dispatch within the local application; it does not
imply upstream billing idempotency.

The browser refreshes status only while its relevant runs are nonterminal, with bounded backoff, and
stops on completion/unmount/hidden-state policy. No idle recipe/history polling. Keep pending operations
in origin-keyed state; responses update their captured run/draft ID, not whichever recipe is selected.

Cancellation records the request and cancels the local task using conditional status updates. Late
completion cannot change a cancelled terminal row. If success already committed, return that result.
Explain that stopping a request cannot guarantee the upstream service did not process or bill it.
Shutdown closes tasks/session and marks interrupted work when possible; lease recovery covers abrupt exit.

Do not automatically retry decision POSTs after timeouts or uncertain network failures. Show a clear
**Run again** action. Keep provider status/request ID where available to help diagnose errors.

### Response validation

Preserve the bounded raw JSON response and create a separate normalized presentation object. Validate
answer types and keys against the submitted questions; validate finite probabilities/confidence in 0–1,
Choice option membership, Score range/legend shape, and required fields. Allow documented rounding in
distribution sums. Preserve unknown metadata for raw export rather than losing it.

A malformed or incomplete answer is a visible provider-response failure/diagnostic, never a fabricated
zero, "No", or success. Missing usage remains unknown, not free. Missing confidence remains unavailable.
Keep raw values separate from rounded display strings.

## 9. Results experience

- **Choice:** headline selected label, all option probabilities in recipe order, and separately labelled
  confidence. Long labels wrap; small categories remain visible.
- **Noul:** **Probability of yes**, with complementary No shown as a derived UI value. No invented
  confidence field and no interpretation of 0.5 as medium severity.
- **Score:** fractional position on the original ordered rubric, labelled endpoints/levels, distribution,
  and confidence. Retain the exact 0-based scale; do not silently convert it to an arbitrary percentage.
- Confidence help text explains concentration across alternatives, not empirical percentage correct.
- Show call duration, returned model, provider, tokens, and USD cost with sufficient precision for tiny
  calls. Expand raw response and compiled request on demand.
- Jev supplies no reasoning trace. Do not display an invented rationale or automatically call a chat
  model to explain the answer. The recipe criteria remain available as context.
- Editing input or questions marks an existing result **From an earlier input/recipe**. Keep its snapshot
  visible until a new call succeeds; a failed rerun must not erase the previous result.
- History details use captured labels/criteria, even if the recipe is renamed or deleted.
- Avoid default "safe to automate" badges or universal confidence cutoffs. User-defined interpretation
  rules and calibration dashboards are later work.

## 10. AI-assisted recipe creation and improvement

Use a compact dialog/drawer with a plain-language goal, a separately chosen generation model, and an
optional example input. Do not mutate the application's active chat model or silently include unrelated
chat/project content. Show what context is being sent; example content is included only when selected.

Call `ctx.chat_completion` with `context={"tools": "none", "nohistory": True, "nostore": True,
"user": authenticated_user}`. Follow existing schema-generation patterns without depending on core_tools.
Reject known non-text/decision-only generation models; allow existing custom text-provider conventions.

Prompt assets specify the portable recipe contract, supported input-schema subset, Jev primitives,
independent-question rule, labelled uncertainty options, and authored-example provenance. Request one
complete JSON recipe. Parse either plain JSON or a single JSON fenced block; validate deterministically.
Use provider structured output only where already supported, with the same validation in all cases.

On invalid output, expose specific repair diagnostics and an explicit **Repair draft** action; avoid
unbounded automatic repair loops or hidden extra paid calls. Aggregate and display generation usage
when available. The generated draft does not execute Jev or save itself.

For improvements, capture recipe ID/local draft key and edit revision before awaiting. Present a focused
summary/diff of input fields, questions, and criteria. **Apply changes** updates the captured draft only
if its edit version still matches; otherwise offer a separate draft/copy. Changes affecting examples
flag those expectations for review. A successful AI call never overwrites intervening manual edits.

## 11. Persistence and API surface

Use `ctx.get_user_path(authenticated_user)` to locate an extension-owned `jev/` directory. Resolve
ownership on every API handler; never trust a client-supplied user or file path. Use the existing default
user behavior when authentication is disabled. SQLite is unnecessary for this personal recipe workflow.

| JSON path beneath `jev/` | Contract |
|---|---|
| `recipes/<filename>` | Plain portable recipe content; filename stem is its unique ID |
| `index.json` | Initialization marker, recipe revisions/content hashes/timestamps/template identities, stars |
| `history/<stem>/<stem>-00001.md` | Readable Markdown plus full JSON recipe/input/request/response snapshots and private submission/lease metadata |
| `history/_drafts/_drafts-00001.md` | Decisions originating from unsaved recipes |
| `.lock` | Cross-process OS file lock covering short read/modify/write operations |
| `.renames.json` | Recovery of pending renames from the earlier name-based store; new display-name edits do not rename files |

Write UTF-8 JSON to same-directory temporary files, flush and fsync, then atomically replace. Hold the
per-user lock across revision checks, idempotent submission lookup, active-run admission, cancellation,
completion, and deletion. Use POSIX flock and Windows msvcrt without new dependencies. A conflicting
operation returns 409 for retry; no upstream request starts before its snapshot is saved. Detect
externally edited recipe documents through content hashes and advance revisions; this also recovers
index metadata if a process stops between a document and index write. Preserve unknown saved data on
errors rather than overwriting a malformed file. Validate recipe/run identities before constructing paths.

At first initialization, read an existing `jev.sqlite` in read-only mode and copy owned recipes,
favourites, and run snapshots to JSON. Starred legacy templates become editable owned recipes; historical template runs keep stable import
references without populating the library;
run snapshots remain exact. Retain the original database as a backup, set the initialization marker,
and never reimport deleted migrated records. A fresh user gets only Message sentiment.

Examples live in the portable recipe document. Run summaries omit large payloads and private lease or
submission fields; retrieve full content for a selected run. Use stable cursor pagination with a capped
limit. Histories use one file per decision to avoid rewriting an ever-growing history array on each poll.

Recipe deletion leaves run snapshots intact. Provide individual history deletion and explicit clear
history controls. Do not silently impose data retention or export input/history with a recipe.

Proposed routes, all under `/ext/jev`:

| Method | Route | Contract |
|---|---|---|
| GET | `status` | Availability, actionable setup state, supported decision models/schema capabilities; no key |
| GET | `recipes` | Owned editable recipe summaries and favourites |
| GET | `templates` | Bundled import collection, including existing owned copy IDs |
| POST | `templates/{id}/import` | Copy template into user storage, or return existing edited copy |
| GET | `recipes/{id}` | Full owned recipe |
| POST | `recipes` | Validate and create a personal recipe; server-owned ID/revision |
| PUT | `recipes/{id}` | Replace portable content with expected revision; 409 on conflict |
| DELETE | `recipes/{id}` | Delete an owned recipe |
| PUT | `favourites/{key}` | Set/unset favourite for an accessible recipe |
| POST | `validate` | Validate draft/input and return compiled request or field diagnostics; no upstream call |
| POST | `runs` | Snapshot draft/input, check submissionId, start bounded execution, return accepted run |
| GET | `runs` | Paginated lightweight history, optionally scoped to a recipe |
| GET | `runs/{id}` | Status or full owned run detail |
| POST | `runs/{id}/cancel` | Request cancellation; return authoritative current state |
| DELETE | `runs/{id}` | Delete a terminal owned history record |
| DELETE | `history` | Clear terminal history after explicit UI confirmation |
| POST | `generate` | Return a validated new recipe draft and generation usage |
| POST | `improve` | Return a proposed replacement draft and generation usage |

Use existing error envelopes. Return appropriate HTTP statuses for validation (400), missing records
(404), revision/submission conflicts (409), local concurrency/rate limits (429), and upstream failures.
Attach structured field diagnostics without breaking the existing error parser; verify how the managed
extension wrapper handles JSON `HTTPException` bodies before choosing the exact serialization. Successful
validation responses can carry diagnostics explicitly. Avoid leaking credentials in provider error text.

## 12. Examples and recipe checks

An example contains an ID, label, input object, optional expectations keyed by question, provenance,
and notes. Choice expectations use option keys; Noul expectations use a human binary label; Score
expectations use a named level/index or explicit acceptable range. Do not store confidence as truth.

**Load example** copies its input into the current input draft without running. **Save as example**
captures the current input and allows manual expected labels. **Check examples** validates all cases
first, states the number of calls, and runs them sequentially through the same run API with cancellation
between cases. Capture the recipe revision for the entire check and retain each completed result.

Show actual versus expected in a compact comparison table. Do not call unlabeled examples "passed" or
compute misleading accuracy from them. If rubric labels/options change, warn on incompatible expectations.
AI-proposed labels remain suggestions until reviewed. A separate durable batch engine is unnecessary for
this small interactive check; reload recovery can restore completed calls and mark unfinished work.

## 13. Planned file layout

```text
llms/extensions/jev/
├── PLAN.md
├── README.md
├── __init__.py                  # Install hooks, authenticated routes, lifecycle wiring
├── schema.py                    # Recipe/input/question validation and request compilation
├── storage.py                   # Per-user JSON files, SQLite migration, revisions, run transitions
├── client.py                    # OpenRouter decisions adapter and response normalization
├── execution.py                 # Bounded run tasks, ownership, cancellation, lease recovery
├── generation.py                # AI authoring, parsing, validation, usage
├── prompts/
│   └── create-recipe.md          # Shared contract for create, improve, and explicit repair
├── recipes/                     # Curated portable JSON recipe assets
└── ui/
    ├── index.mjs                # Lightweight icon and lazy route registration
    ├── JevPage.mjs              # Page shell and view composition
    ├── RecipeLibrary.mjs
    ├── ImportRecipeDialog.mjs     # Searchable bundled recipe collection and JSON file import
    ├── RecipeEditor.mjs
    ├── InputSchemaEditor.mjs
    ├── QuestionEditor.mjs
    ├── JsonEditor.mjs           # Lazy CodeMirror with native textarea fallback
    ├── StudioIcon.mjs           # Consistent native SVG icons
    ├── DecisionResults.mjs
    ├── RecipeExamples.mjs
    ├── RunHistory.mjs
    ├── AiRecipeDialog.mjs
    ├── recipeModel.mjs          # Pure draft transforms/validation/compilation helpers
    ├── studioState.mjs          # Origin-keyed operation state and API coordination
    └── draftStore.mjs           # Account/server-scoped IndexedDB recovery
```

The authoring model selector is the global `ModelPicker` in `llms/ui/components/ModelPicker.mjs`,
shared with agent profiles and Gemini. Main chat retains its original selector. See `docs/SHARED_CONTROLS.md` for its contract.

Names are implementation guides, not a requirement to create empty scaffolding. Split modules when
they own distinct behaviour; avoid putting the whole extension into one giant page module.

## 14. Implementation sequence and completion gates

### Phase 1 — Contracts, starter content, and storage

- Finalize the version-1 recipe/input contracts and current upstream limits.
- Audit `JsonSchemaForm` capabilities and choose a supported subset it actually renders well.
- Implement deterministic compilation/validation and representative shared fixtures.
- Implement the per-user database, additive migrations, revisions, and immutable run records.
- Author the curated starter assets and validate every one through the same code as imports.

Gate: each starter produces a valid Jev request from its example inputs; quote/newline/Unicode input
round-trips; bad contracts receive useful diagnostics; ownership and concurrent saves are correct.

### Phase 2 — Polished read/run experience

- Build lazy page registration, recipe library, setup states, forms, and responsive layout.
- Add the provider adapter and bounded run lifecycle, including idempotency and recovery.
- Implement all three answer types and raw/call-detail disclosure.
- Add input draft recovery, origin-scoped async updates, and history replay.

Gate: a user can run each starter, navigate away/back, recover its result, and understand probability
versus confidence. Missing keys and provider failures have actionable UI. No idle network polling.

### Phase 3 — Recipe authoring

- Add custom copies, visual field/question editing, option/scale reordering, and save/conflict flows.
- Add the lazy advanced JSON editor and request preview with explicit apply semantics.
- Add validated import/export and favourites.

Gate: users can create a useful recipe without JSON; visual/JSON changes preserve meaning; invalid
advanced edits cannot execute accidentally; bundled templates and recorded history cannot be modified by editing a personal recipe.

### Phase 4 — AI assistance and examples

- Implement create/improve prompts, separate generation model selection, and draft validation.
- Add proposed-change review, explicit repair, and stale-generation protection.
- Add example management, expectation provenance, and cancellable sequential checks.

Gate: natural-language goals produce editable recipes; manual edits survive late AI completion;
example checks report actual results without presenting generated labels as verified truth.

### Phase 5 — Visual review and release verification

- Exercise loading, empty, long-content, ambiguous-result, conflict, interrupted, and malformed-response
  states in light/dark themes at representative widths.
- Review keyboard focus, accessible labels, menu/dialog behaviour, probability charts, and scale legends.
- Verify packaged assets, extension disable behaviour, and browser/native ESM operation.
- Document setup, recipes, question types, model pinning, local data, history deletion, and export.
- Run targeted tests and impacted existing checks; inspect the final diff for unrelated edits.

Gate: all acceptance scenarios below pass, screenshots show no clipping/overlap, and the extension's
core journey needs neither developer tools nor manual JSON editing.

## 15. Verification strategy

Use stdlib unittest, the repository's Node test conventions, and its headless Chromium fixture pattern.
Mock upstream requests for automated checks. A live paid API call is a separate explicit smoke test,
not a unit-test dependency. Browser fixtures label sample results as fixtures, not live measurements.

### Python tests

- Valid/invalid recipes and schema/input constraints, including unsupported schema features.
- Correct Choice/Score/Noul compilation and input escaping; server-owned field stripping.
- Per-user isolation, recipe revisions/conflicts, favourites, import identities, and deletion semantics.
- Immutable history after recipe edits/deletion; pagination and snapshot hydration.
- Mocked OpenRouter success, authentication failures, credit errors, rate limits, timeouts, oversized
  responses, malformed JSON, missing answers, and inconsistent types/options.
- Idempotent submission; no duplicate upstream dispatch; cancelled/late completion races; restart lease
  recovery; concurrency limits; no automatic retry after uncertain provider outcomes.
- Generation validation, fenced/plain JSON parsing, separate text-model selection, and absent tools/history.

### Frontend tests

- Shared recipe fixtures, compilation parity, non-destructive JSON apply, option/reference updates.
- Separate input/editor draft identities, account/server namespacing, and draft recovery.
- Late run/save/generation responses while switching recipes or editing the origin.
- Probability/confidence formatting, Noul semantics, fractional Score labels, missing cost/usage handling.
- History snapshot labels, dirty-result markers, and user-reviewed example expectations.

### Browser acceptance scenarios

1. New user with no key can browse/edit recipes and reach the existing provider setup.
2. Configured user tries a starter with one example-load action and one Run action.
3. All primitives render clearly, including uncertain distributions, tiny probabilities, long labels,
   and intermediate scores.
4. Visual editing, advanced editing, import/export, favourites, and conflict recovery work by keyboard.
5. Switching recipes during a run or AI generation does not move input/results into the wrong workspace.
6. Reload restores input and finds in-flight/completed/interrupted runs correctly.
7. Broken upstream responses display useful errors and retain prior successful results.
8. A custom recipe can be created from a plain-language goal, reviewed, saved, and tested against examples.
9. Light/dark and narrow/wide layouts have no inaccessible controls, clipped labels, or accidental overflow.
10. Chat history, composer drafts, project selection, and global chat model selection remain correct.

Add tests that exercise these contracts and failure modes, rather than tests that merely reproduce the
implementation. Do not broaden unrelated testing after relevant checks pass unless a change warrants it.

## 16. Implementation assumptions and points to verify

- The initial execution provider is OpenRouter; use the configured server-side provider credential policy.
- Page naming is Decision Studio, while files, API namespace, and extension toggle remain `jev`.
- User recipes/history live in extension-owned per-user JSON files; unsent drafts remain browser-local.
- Latest is convenient for exploration; pinned releases remain selectable for tested recipes.
- Simple object/text state construction is sufficient for the initial curated library.
- Existing form rendering, error envelopes, model-picker selection behaviour, and packaged asset patterns
  must be checked in implementation before adding helpers or claiming broader schema support.
- Jev's alpha endpoint may change. Keep the adapter isolated, verify current docs before coding it, and
  preserve raw responses alongside normalized UI data.
- The conversation mockup in `work/jev-planning/decision-studio.html` is a planning reference, not a
  production component or bundled asset. Implement the final UI using actual application styles.

## 17. Upstream references

Verified during planning on 2026-10-01; recheck the live contract during implementation.

- [OpenRouter Jev model and latest alias](https://openrouter.ai/~typesafe/jev-latest)
- [OpenRouter Jev tutorial](https://openrouter.ai/docs/guides/community/jev-tutorial)
- [OpenRouter Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)
- [TypeSafe question primitives and independence](https://docs.typesafe.ai/primitives)
- [TypeSafe confidence semantics](https://docs.typesafe.ai/confidence)

Jev receives state and typed questions, returns typed answers, and does not supply a reasoning trace.
Choice and Score include distributions and confidence; Noul supplies the probability of yes. These
semantics must remain visible in the UX and intact in the recorded run data.


## 18. Implementation delivery notes — 2026-10-01

The first-release scope is implemented in this extension. Seven bundled recipe templates each include two contrasting illustrative inputs; a new user’s library
starts with only an editable copy of Message sentiment. The page uses the existing `JsonSchemaForm` directly, and the shared
creation prompt covers create, improve, and explicit repair without duplicating the recipe contract.
At phone widths the library becomes a horizontally scrollable recipe picker rather than a separate
drawer, keeping search and the selected recipe visible without another modal interaction.

Completed capabilities:

- Lazy icon/page registration; responsive light/dark shell; setup, empty, error, and interrupted states.
- Library search, stars, personal recipes, visual fields/questions/options/scale editing, labels, and
  optimistic save conflicts with copy/reload paths.
- Validated JSON application, request/curl preview, portable imports/exports, scoped draft recovery,
  cross-tab conflict copies, and preserved pending/results when saving an in-flight local draft.
- Authenticated per-user APIs, server-only OpenRouter credentials, immutable JSON run snapshots,
  bounded execution, active concurrency limits, cancellation, expired-lease recovery, and idempotency.
- Ambiguous browser submission recovery reuses the original submission ID and captured payload.
- Actual Choice/Score/Noul distributions, optional confidence, fractional scores, tiny usage costs,
  stale results, retained prior success on rerun failure, and raw-response disclosure.
- Centered AI dialog using Project Manager checkbox/input styling, a Gemini-style searchable model
  picker with provider filters, pricing/context cards, sorting, and nested-dialog focus handling.
- Separate AI-authoring model, proposed recipe review, manual repair, explicit apply, and stale-response
  protection. Every generated example retains AI-suggested provenance until manually reviewed.
- Sequential example checks, reviewed expectations, earlier-question markers, history pagination,
  detailed snapshots, replay, export, and deletion.
- A route wrapper keys the workspace by server/account so an authentication change remounts its state.

Verification uses the focused Python storage/API checks, six Node contract checks, and a real-component Chromium fixture.
The browser fixture covers missing credentials, creation/customization, JSON errors, all answer types,
AI drafting, example checks, late response ownership, saving running drafts, idempotent recovery,
history, remounts, and actual IndexedDB account isolation/conflict recovery. Provider calls are mocked;
no live paid OpenRouter decision or generation call is part of this verification. Packaging was checked by building a wheel in an isolated temporary directory; all
33 extension source, UI, stylesheet, recipe, prompt, and documentation files were present.

Limits retained from the first-release plan: no batch CSV workflow, autonomous downstream actions,
provider selection beyond OpenRouter, calibrated acceptance thresholds, or support for upstream
structured criteria. The form/schema contract deliberately rejects unsupported features rather than
silently changing a request. Subsequent provider/API model updates can extend the explicit model list.


## 19. JSON storage and editable library revision — 2026-10-02

Replaced the recipe/history SQLite store with `storage.py` and per-user JSON documents. Added one-time
read-only migration, atomic writes, cross-process locks, external-edit revision detection, template
identity tracking, and repeat-import preservation. Removed every read-only starter path from the UI;
Save, Edit, Examples, Improve with AI, and Delete operate directly on personal copies. The import modal
browses bundled templates and retains portable file import. Message sentiment contains one input titled
**Email, tweet or comment**, with no subject field in its schema or examples.

Verification includes initialization/deletion behavior, plain recipe files, migration with original DB
preservation, import identity and user isolation, stale revisions, interrupted writes, locking, immutable
snapshots, run idempotency/cancellation, and actual browser collection search/import/edit/reopen flows.

## 20. Recipe filenames as identities — 2026-10-02

Recipe JSON filename stems, excluding the final `.json`, identify recipes and history references. File imports preserve
uploaded filenames, and bundled filenames no longer have numeric prefixes. Editing a display name
keeps the filename and ID unchanged; different files may share a display name. Existing files stay
as-is while metadata, stars, history, and browser drafts migrate to filename stem references.
Collection and file imports warn before replacing an existing filename, verify its revision, and
clear matching history on confirmation. Cancellation preserves both definition and history.
Automatic account initialization is idempotent, preserves existing defaults and retained history,
and never uses the explicit replacement flow. Duplicate and Save as a copy choose unused filenames.
Initialization writes a receipt outside `jev/`; deleting that folder starts fresh with sentiment only
and empty history rather than reimporting the surviving legacy SQLite backup. Reference migration
journals history targets so an interrupted migration can safely resume, including filenames with dots.


## 21. Readable history identifiers — 2026-10-02

New history records use `<recipe-id>-00001` identifiers and matching `.md` filenames. Unsaved
recipes use `_drafts-00001` under `history/_drafts/`. Markdown shows the summary, input, and answers,
with the complete structured JSON record embedded for exact reload and run transitions.
Per-recipe counters are reserved under the existing file lock, using the highest known suffix plus one.
Counters survive history deletion and recipe replacement, and reservations survive failed record writes.
Retries of an existing submission return its original ID without consuming another number.
Existing UUID JSON history remains readable through the same APIs; recipe and reference migrations
preserve both file formats. The studio keeps chronological ordering and cursor pagination.
