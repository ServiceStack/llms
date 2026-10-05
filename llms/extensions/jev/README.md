# Decision Studio

Decision Studio is a dedicated Jev workspace in the left icon bar, also available at `/jev`.
Choose a recipe, supply its input, and get typed decisions with probabilities. It uses
[OpenRouter's Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request), separately from chat completions.

## Get started

1. Start llms.py normally and open **Decision Studio** using the decision tree icon.
2. Open **Message sentiment** and click **Try example**, or enter an email, tweet or comment.
3. Configure an OpenRouter API key through **Models & providers**, or provide
   `OPENROUTER_API_KEY` when starting the server. OpenRouter must be enabled.
4. Click **Run decision**. Inspect the results, distributions, question criteria, and call details.

You can browse, edit, import, and save recipes before connecting OpenRouter. Running a decision
sends its displayed input and questions to OpenRouter. **Check examples** makes one call per case.

New users start with one editable **Message sentiment** recipe. **Import recipe** opens a searchable
collection of the JSON files bundled in this extension's `recipes/` directory: message sentiment,
support triage, company news, email intent, feedback tags, audience relevance, and claim support.
Importing saves your own copy immediately. JSON filename stems are unique identifiers: `sentiment.json` uses `sentiment`. Imported files keep their filenames and recipe contents; display names can be shared.
Importing a collection recipe or JSON file with an existing filename warns before replacing it and clearing its old history.
Cancel the warning to keep the existing recipe and history. **Import from file** validates and saves a portable JSON
recipe in your library. Company news assesses reported business implications rather than predicting prices.

## Make it yours

Every recipe in the library belongs to you and is editable. In **Edit**, define the input form and
add questions using three answer types:

| Type | What to define | How to read the answer |
| --- | --- | --- |
| Yes / no · Noul | A focused question and optional descriptions of yes and no | `noul` is the probability of yes, from 0 to 1 |
| Choose one · Choice | Distinct named alternatives and criteria | A selected option and a distribution across all alternatives |
| Ordered scale · Score | 2–10 ordered descriptions, from low to high | A fractional score from 0 to the last level, plus probabilities for each level |

Choice and Score confidence describes the concentration of the distribution. It is not measured accuracy.
Jev returns typed decisions, without a free-form reasoning explanation.

Give options readable display labels; their stable keys remain available for integration. Changing a
question's meaning clears affected expected answers. Changing an input field's type clears examples
that no longer fit the form. Trash buttons in each field and question header delete the item
and its related example data. Recipes must retain at least one question.

Use **Recipe JSON** for advanced changes. Edits become active only after **Apply JSON** succeeds.
Invalid or unapplied JSON cannot be saved or run. **Check recipe** validates the definition without a
provider call and sits immediately before **Save recipe** in the recipe header. The **Run** view shows the compiled request and an exportable curl command with an API
key environment-variable placeholder.

**Save recipe** persists personal recipes on the server. If another tab saved a newer revision, save your
edits as a copy, or use **Reload saved recipe** from the recipe menu. Unsaved input and recipe drafts
recover in the same browser. Search, tags, and stars help keep frequently used recipes close.
Editing a saved recipe's display name leaves its filename, identifier, stars, and history references
unchanged. Recorded snapshots keep their original contents. Duplicate and Save as a copy choose an
unused filename. Filenames must be valid portable JSON filenames (spaces and Unicode are supported).
New recipes initially use a filename derived from their display name; uploaded recipes use the uploaded filename.

## Create or improve with AI

Describe your goal using **Create with AI**, or ask for a specific change through **Improve with AI**.
Use the model picker to search by name, ID, or provider; filter providers; and sort model cards by
release date, price, context limit, or name. The global picker initially shows the newest 20 models,
then adds 20 as you scroll. Cards show provider, pricing, context, and available model
metadata. This choice is independent of the Jev decision model and the global chat selection.
Review the proposed fields, questions, and full JSON before applying the draft. Invalid generated JSON
has an explicit **Repair draft** action; there are no hidden retry calls.

Generation sends the goal and, when improving, the recipe definition, including any saved examples.
Your current input is included separately only if you enable **Use my current input to help design the recipe**.
Chat history, tools, and project files are not included. AI generation creates no examples or expected
answers. Improvements preserve existing examples that still fit the input form and clear their outputs
when the executable definition changes. Late AI proposals never apply themselves over edits.

## Examples and history

Run the recipe with an input, then choose **Save as example** in Results. A dialog suggests a descriptive
name using the text model configured in `defaults.summarize` in `llms.json`. Edit that suggestion and
choose **Save example**; you can also rename it directly in the Examples tab. This single text-model
request uses the recorded input and normalized results, not chat history or raw provider responses.
If naming is disabled, unavailable or fails, enter your own name or use the fallback. Typing a name
prevents late suggestions from overwriting it. Cancelling the dialog creates no example.

The example captures
that run’s original input, normalized answers, probabilities, model and completion time, even if
you have since edited the form. Save the recipe to persist it. This makes no extra Jev decision call
and does not change run history. Saved outputs are included when exporting or sharing.

Changing the input schema, state mapping, questions or decision model clears saved outputs from
examples, retaining their inputs for another run. Original run history remains available.

New examples can only be added by saving successful runs; there is no manual example or expected-answer
editor. Keep contrasting recorded examples in **Examples** and check cases sequentially against their saved
output. Existing imported examples and their expected answers remain readable. Comparisons show matches
and differences rather than claiming a benchmark score. Noul comparisons use 0.5 and Score comparisons
allow half a level, for inspection only. Stop checks to prevent subsequent calls.

**History** contains immutable copies of the recipe, input, request, response, model, and usage for every
submitted decision. Open a record to inspect it, export it, or use its recipe and input as a new draft.
Deleting a personal recipe retains its recorded decisions. Delete individual completed records or use
**Clear history** to remove completed history; active decisions are kept.

Calls continue on the server when you leave the page. Only active calls are polled. **Stop** cancels local
tracking/execution, but OpenRouter may already have processed the request. Interrupted or failed calls
are never automatically resubmitted. If the browser loses the submission reply, **Retry submission**
reuses its identifier and captured input to recover the same record without dispatching a duplicate.

The model picker offers latest, version, and dated aliases. Use a pinned model when comparing recipe
behaviour over time; results retain the provider's resolved model when it is returned.

## Portable recipe format

Export/import uses plain JSON with `schemaVersion: 1`. Definitions contain:

- `name`, `description`, optional single `content`, up to three `tags`, and `decisionModel`.
- `inputSchema`: an object-root JSON Schema subset used to build and validate the input form.
- `state`: `{ "mode": "object" }`, or `{ "mode": "text", "field": "text" }` for a top-level text field.
- `questions`: stable keys with `type`, `instructions`, and `criteria`.
- Optional `presentation.questions`: result labels and Choice `optionLabels`.
- Optional `examples`: input cases, expected answers, provenance, and an optional recorded `execution`.
  Recorded executions contain `status`, `input`, `prompt`, `answers`, `model`, `completedAt`,
  and optional `durationMs`; they must be successful, match the example input, and contain all
  normalized question results. Raw responses, private fields and usage are excluded.

See [the bundled recipe files](recipes) for complete importable examples. Input schemas support objects,
strings, numbers, integers, booleans, and arrays of primitive values; enums, required fields, defaults,
length/numeric/list bounds, and `textarea`, `date`, or `email` hints. Dates and email addresses are display
hints. Nested schema editing uses Recipe JSON. Remote references, executable templates, and unsupported
schema keywords are rejected. Documents are limited to 512 KB; recipes allow 32 questions and 30 examples.

## Storage and architecture

Jev stores JSON recipes and Markdown history under `$LLMS_HOME/user/<username-or-default>/jev/` (the default data root is
`~/.llms`):

```text
jev/
  recipes/<filename>                      # e.g. sentiment.json
  history/<stem>/<stem>-00001.md           # e.g. history/sentiment/sentiment-00001.md
  history/_drafts/_drafts-00001.md         # Decisions from unsaved recipes
  index.json                              # Revisions, stars, template identities, history counters
  .lock                                   # Short-lived cross-process coordination
```

New users receive only a personal copy of the bundled `recipes/sentiment.json`. Deleting it does not recreate it;
you can import it again from the collection. Recipe deletion retains history. JSON file writes are
atomic, and the file lock serializes revision checks, submissions, cancellations, and history deletion.
Replacing a recipe requires confirmation of its current revision and clears only that file's history;
stop its active decisions before replacing it. Recreating a deleted filename also warns before clearing
retained history. Existing files remain as-is; metadata, stars, browser drafts, and history references
migrate to filename stems without the final `.json` extension. Files with duplicate display names remain separate recipes.
Automatic first-account setup never replaces an existing default file or clears history.
History IDs use the recipe filename stem and a per-recipe sequence, padded to at least five digits.
Each Markdown file includes a readable summary, inputs, results, and the complete JSON record.
The next number follows the highest existing sequence or reserved counter, allocated under the file
lock. Deleting history or replacing a recipe keeps the counter so old references are not reused;
resetting the whole `jev/` folder resets counters. Existing UUID JSON history remains accessible.
The studio continues to show history newest first.
Edits to a recipe file on disk are detected through content hashes and participate in save conflicts.
Reload the saved recipe in the UI after editing it externally.

If an older `jev.sqlite` exists, Jev migrates its recipes, stars, and decision snapshots once. Previously
starred built-in recipes become editable copies; past runs retain their full snapshots without
adding unused templates to your library. The original database remains untouched as a
backup and is no longer used after migration. A receipt at `user/<username>/.jev-initialized.json`
survives deletion of the `jev/` folder, so a reset creates only `sentiment.json` with empty history.
Deleting migrated items does not import them again.

- Browser input/recipe drafts and run references: IndexedDB `llms-jev-drafts`, separated by server and authenticated account.
- Conflicting edits from different tabs preserve the original draft and one recovery copy of your edits.
- OpenRouter credentials stay on the server. No new runtime dependencies or frontend build step are required.
- Extension routes live under `/ext/jev`; UI and stylesheet load when `/jev` opens.
- Requests use the fixed OpenRouter Decisions endpoint with bounded response sizes and timeouts.
- Each user can have two active decisions. Shutdown or expired leases mark unfinished requests interrupted.
- Add `jev` to the existing `disable_extensions` configuration to disable the extension.

The [technical plan](PLAN.md) records the contracts and implementation decisions. Python modules handle
validation, storage, execution, and AI authoring; Vue ESM components own the library, editors, examples,
and result views. This extension does not change chat histories, composer drafts, or project membership.

## Recipe sharing

Open **Share recipe** after saving and successfully running a recipe. Choose a matching local run
and review **Preview** or **JSON** before publishing through your connected llmspy publisher account.
The saved recipe and selected run’s **inputs, compiled prompt and normalized results become public**.
Authored usage examples, including their sample inputs and expected answers, are part of the recipe
and are always included. Recorded usage examples include their explicitly saved outputs. Other history and raw provider
responses stay local. Adding or editing usage examples alone does not require another execution
before sharing; other recipe edits need a matching successful run. Sharing makes no provider calls.

Public links at `https://ai.llmspy.org/d/{reference}` show the recorded result immediately, without
provider credentials. **Update shared recipe** explicitly replaces the public snapshot while keeping
the link. Local edits never push automatically. **Stop sharing** removes future public access; downloaded
or imported copies remain usable. Deleting or replacing the local recipe leaves its public share
available, so manage it separately through **Stop sharing** or the gallery’s **My recipes** view.

Use **Import recipe → Collection** to browse published recipes. **From JSON** accepts a JSON export
URL or a JSON file. Share links such as `https://ai.llmspy.org/d/zoL2HG` automatically download
`https://ai.llmspy.org/d/zoL2HG.json`; other URLs must return the portable recipe JSON export.
Built-in collection imports are no longer available.

URL and Collection imports save an independent editable copy directly. Collection entries provide a
**View** link to the public page. Filename conflicts offer replacement
(which clears that recipe's history), a different filename, or cancellation. Configured publisher
links retain attribution and a separate **Published example**, including its original recipe snapshot.
Copying its input into the form is explicit, and it never counts as a local execution.

Publisher credentials come from the authenticated user’s existing `share_llmspy/config.json` on the server
(`$LLMS_HOME/user/{username}/share_llmspy/config.json`, or `~/.llms/user/{username}/share_llmspy/config.json`). Connecting an account shows the requesting host
and requires an explicit grant. Public browsing/importing does not require a publisher key. With the
share_llmspy extension disabled, local Jev remains usable and sharing reports its unavailable state.
The [sharing implementation plan](SHARING_PLAN.md) records contracts and recovery rules. Deploy
ubixar’s migration, APIs and public viewer before releasing the Jev client.

## UI styling

Use Tailwind utilities directly in Vue templates, including responsive, dark, and state variants.
Shared form/editor children use scoped arbitrary variants. Styles are generated in `llms/ui/app.css`
from `llms/ui/tailwind.input.css`; Jev has no separate stylesheet.

## Verification

From the repository root:

```sh
python -m unittest tests.test_jev tests.test_jev_sharing
node tests/test_jev_model.mjs
python tests/verify_jev_browser.py --screenshot /tmp/jev-light.png
python tests/verify_jev_browser.py --width 390 --dark --screenshot /tmp/jev-phone.png
python tests/verify_jev_browser.py --fixture jev-sharing
python tests/verify_jev_browser.py --fixture jev-sharing --width 390 --dark
python tests/verify_jev_browser.py --fixture jev-public-gallery
python tests/verify_jev_browser.py --fixture jev-navigation
python tests/verify_jev_browser.py --fixture jev-navigation --disable-publish
```

Use a Python environment with the application's dependencies installed. The browser check requires
Chromium and uses the actual Vue components, forms, editors, and IndexedDB with mocked provider results.
It makes no paid calls. Automated checks cover input/response boundaries, user isolation, concurrent
saves, immutable records, idempotent submission, cancellation, AI provenance, recovery, and origin ownership.

The shared contract corpus in `tests/fixtures/jev-sharing-contract.json` is also consumed by ubixar’s
`DecisionPublishTests`. `tests/verify_jev_sharing_browser.py` checks an isolated development publisher
and public viewer; its mutation checks accept only loopback fixture hosts. No production fixtures
or production database migrations are part of these checks.

### Discovery tags and usage

Content types and tag suggestions come from the configured publisher (ai.llmspy.org by default)
through `/ext/jev/tags`. The UI caches the public catalogue for 24 hours and retains stale
suggestions offline. Each catalogue entry has one label, used for display, selection and saved
metadata; there is no separate name or slug. Custom comma-separated labels retain their spelling
and spaces. Older lowercase/hyphenated values still match the corresponding labels. The version 3
cache refreshes older catalogues immediately. Choose one content type and up to three relevant tags.

Publishing captures the publisher's local favourite flag and recorded execution count.
The count includes retained local runs except pending submissions; imported worked examples
are excluded. Clearing history reduces the next published count. Usage refreshes on publish
or update and is frozen with the snapshot during recovery. The Collection import browser
supports tag filtering and recommended/most-run/newest/name ordering. Recommended ranks
total stars, then recorded runs, then update time. Most run orders by runs, then stars. Publisher
run counts are private sorting signals and are omitted from public responses and displays.
Signed-in readers can star or unstar a recipe in the gallery, public viewer, or Jev Collection.
Each person counts once; the publisher's local favourite contributes one star without double-counting
a separate star from that same publisher. Imported recipes start with their own local usage.

Deploy the corresponding sharing server with Migration1011 for usage metadata and Migration1012 for community stars.

The metadata editor has a searchable **Content** input for one content type and a searchable
**Tags** input for up to three discovery tags. Both support custom values. Enter or comma adds
a tag; a chip’s close icon removes it. Picking another content type replaces the existing one.
Older mixed metadata is displayed in the separate fields and converted when you edit it.
Existing mixed-tag documents remain valid so saved files, earlier shares and pending publications
can still load or recover; new metadata uses the three-tag limit.
