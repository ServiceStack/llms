# UI startup performance

## Findings and changes

The original bootstrap awaited every UI extension import, installation and startup `load()` request
before mounting Vue. An empty `#app` remained on screen throughout. Optional features enlarged this
critical path: CodeMirror's core was 402 KB and parser-blocking; PDF Studio's entry was 162 KB;
analytics pulled in its 96 KB page plus 196 KB of Chart.js. Gemini also eagerly imported Chart.js
through its search management panel. The core `/models` response was 409 KB. Sizes here are uncompressed.

The implementation now:

- Paints a theme-aware HTML loading screen immediately and preloads the core ESM dependencies using
  the resolved import map, including the correct debug/production Vue build.
- Mounts a small Vue shell before importing extensions. Imports remain parallel; installation order
  and async installer dependencies are preserved. Malformed or failed imports cannot break sorting.
- Loads PDF Studio, analytics, the calculator and code runner as lazy route modules. Components within
  the loaded PDF and analytics modules register synchronously. Gemini's sources, import, assistants and
  searches panels load together when opening a file-store workspace, then render synchronously; the
  workspace already mounts its hidden tabs with `v-show`. Chat citation filters and the Typst
  grammar remain available during initial registration.
- Loads CodeMirror CSS and scripts on demand. The core loads before modes/addons, and concurrent
  editor consumers share asset requests. Chart.js is absent from initial chat loading.
- Compresses buffered text/JSON responses, including the model catalog and UI assets, using aiohttp.
  Large static response compression runs off the event loop. Streaming chat responses and SSE keep
  their existing streaming behavior.
- Adds ETag revalidation to core and extension text assets. `Cache-Control: no-cache` allows stored
  assets to be reused after validation while checking for changes after an upgrade.
- Starts the two tools API requests concurrently, skips a duplicate initial authentication bootstrap,
  and awaits saved agent prompts before making chat available.

The browser extension was moved to an external extension during this work. It is no longer part of
the in-repository lazy route changes. Its external entry can use the same documented loaders.

## Measurements

Measured locally on 2026-09-30 with headless Chromium, a fresh profile and isolated `LLMS_HOME`, no
model requests, browser caching disabled, 80 ms simulated network latency, 1.25 MB/s download/upload,
4× CPU slowdown, and a 1280×900 viewport. Three reloads were used per benchmark. Extension availability
and local account data affect the result; these are controlled local measurements, not deployment SLOs.

The baseline below is unmodified revision `53bff1e`, after the browser extension was moved out.
Values are medians, rounded. Payload sizes exclude HTTP headers.

| Metric | Before | After |
| --- | ---: | ---: |
| First contentful paint | 4.45 s | 0.34 s |
| Chat rendered | ~4.45 s | 1.65 s |
| Startup response payload over the network | 4.47 MB | 0.74 MB |
| Uncompressed startup response payload | 4.47 MB | 3.20 MB |
| Completed startup resource requests | 83 | 69 |

**The new first contentful paint is the loading screen, not an interactive chat.** Before, chat was the
first content on the page, so its FCP is an approximate chat-render reference. After, the separate
`llms:startup-ready` performance mark is emitted after Vue renders the ready application. On this
fixture the result is approximately 2.7× faster chat rendering and 84% less network payload.
Request counts are sampled shortly after readiness; background updates can affect the exact count.

## Remaining opportunities, in priority order

1. **Split the rest of Gemini's manager from its chat integrations.** Its main entry is still about
   157 KB, and shared metadata/explorer components remain eager. Extract the small citation filter,
   thread header and message footer into a chat module; import store management only on `/gemini`.
   Preserve the citation/document cache and initialization shared by chat and management views.
2. **Separate the ServiceStack UI plugin from feature-only helpers.** The core imports the complete
   311 KB `servicestack-vue.mjs` module. Type generation is only needed by code/PDF tools, but the plugin
   also supplies components used throughout the app. A smaller vendored entry would reduce parse and
   evaluation work; replacing the plugin wholesale would risk shared form/auth behavior.
3. **Offer a compact model bootstrap representation.** `/models` remains 409 KB before compression
   (about 39 KB compressed). The composer primarily needs identifiers, provider, names, capabilities
   and defaults. A smaller additive endpoint plus details on demand could reduce JSON parsing and
   reactive state overhead. Inventory model picker, token/cost, and provider consumers first; retain
   the existing API and avoid populating reactive state with incomplete model records.
4. **Defer settings/profile management components.** Model selection, settings, profile management,
   tools and skill management still ship substantial templates in their entry modules. Extract modal
   and management UI while keeping chat filters and profile/tool state initialization eager.
5. **Avoid repeated anonymous-user setup.** `AppExtensions.on_request()` keys local requests as
   `default` but records `last_seen` only for named users. Local `/config`, `/models`, extension APIs and
   HTML requests therefore repeatedly invoke the projects setup callback. Cache successful setup per
   user, share an in-flight setup task and retry after failure. Test concurrent first requests and
   account isolation before changing this lifecycle.
6. **Measure long conversations separately.** `ChatBody` renders message components and markdown,
   and streaming updates can cause repeated parsing/highlighting. Profile a large stored thread and
   an active stream before implementing virtualization or per-message render caching. Keep complete
   canonical history, tool call/result groups, draft ownership, copy actions and scroll behavior intact.

The earlier opportunities have observed startup payload evidence. Long-thread rendering is a follow-up
profiling target, not a measured bottleneck in the empty-chat startup fixture.

## Checking a change

Use a fresh browser profile and a temporary server data directory. In browser developer tools, disable
the cache, apply the same network/CPU throttling, and collect three reloads. Inspect:

```js
performance.getEntriesByType('paint').map(x => ({ name: x.name, ms: x.startTime }))
performance.getEntriesByType('mark').filter(x => x.name.startsWith('llms:'))
performance.getEntriesByType('resource').reduce((sum, x) => sum + x.encodedBodySize, 0)
```

Check that initial chat loading requests no `pages.mjs`, CodeMirror scripts or Chart.js. Then open PDF
Studio, Run Code, Calculator and Analytics, reload a `/pdf` deep link, and open an extension modal using
`/?open=projects-manager`. For warm-cache behavior, enable caching and check conditional asset requests
and 304 responses. Test a changed asset with the previous ETag to ensure an upgrade returns its new body.

Validation performed: all five JavaScript regression scripts passed; all 277 Python tests passed on
the final full-suite run. A sidebar assertion failed once, then passed alone and on a full rerun; no
database implementation was changed. Real browser checks passed for lazy routes, a PDF deep link with
CodeMirror, and an initial extension modal query, with no browser errors. HTTP tests cover compression,
identity responses, HEAD, ETag changes/revalidation and unbuffered SSE. CI runs these new HTTP tests
across Linux/macOS/Windows on Python 3.11/3.14, and the JavaScript checks in a separate job.
