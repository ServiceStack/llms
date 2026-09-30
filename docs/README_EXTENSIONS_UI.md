# UI Extensions API

The UI Extensions API starts from `AppContext` which is available via the `ctx` singleton provider. An extension-scoped API can be created with `ext = ctx.scope(extensionName)`.

The `ctx` object provides access to the application state, routing, AI client, formatting utilities, and UI layout controls. It is globally available in Vue components as `$ctx` and can be imported in other modules.

## Startup and asynchronous loading

The HTML loading screen paints before JavaScript initialization. `createContext({ deferExtensions: true })`
prepares the built-in modules and router; the entry page mounts the Vue shell, then awaits `ctx.start()`.
Existing hosts calling `createContext()` without this option still receive a fully initialized context.
Extension imports
begin after the shell has a paint opportunity. Imports run concurrently, while `install(ctx)` hooks run
in ascending `order` and are awaited individually, including async installers. Equal orders preserve
the extension manifest order. Failed imports/installers are logged without stopping other extensions.

The bootstrap registers extension components, routes and navigation guards before running `load(ctx)`
hooks concurrently. Chat and the rest of the application render when these hooks finish
(`ctx.state.startupReady`), so a user cannot send a message before saved profile prompts, tools and
request filters are ready. A `load` hook must return/await all work required for that readiness.
`ctx.start()` returns the same promise on repeated calls. Deep links and modal query strings are
resolved after extensions register their routes and components.

Keep an extension's entry module small: register icons, chat filters and lazy views during installation,
then import management pages, editors and chart libraries only when those views are opened. For example:

```js
import { lazyComponent, lazyModule } from '/ui/lazy.mjs'

export default {
    install(ctx) {
        const load = lazyModule(() => import('./pages.mjs'), module => module.init(ctx))
        ctx.components({ ReportDialog: lazyComponent(() => load().then(module => module.ReportDialog)) })
        ctx.routes.push({
            path: '/reports',
            component: () => load().then(module => module.ReportPage),
            meta: { title: 'Reports' },
        })
    },
}
```

`lazyModule` shares one import/initialization across components and permits retry after a failed load.
`lazyComponent` uses [Vue's async component API](https://vuejs.org/guide/components/async) with loading
and error feedback. These are native browser ES modules; no bundler is required. Route components use
the [router's lazy loading API](https://router.vuejs.org/guide/advanced/lazy-loading.html).

For CodeMirror, await `loadCodeEditor(ctx)` from `/ui/lazy.mjs` before rendering an editor. It loads CSS,
the editor global, then its modes/addons, with deduplication across extensions. It leaves the editor
unloaded when `core_tools` is disabled; a consumer can retain its textarea fallback. Avoid parser-blocking
editor/terminal scripts in `add_index_footer()`.

Routes can set `meta: { header: false }` to hide the shared header icons and top panel. Page-specific
toolbars remain inside the page. Header visibility follows the active route, so normal navigation
restores it automatically; `ctx.toggleLayout('header', false)` can also hide it through layout state.

See [UI startup performance](UI_STARTUP_PERFORMANCE.md) for measurements and remaining opportunities.

## AppContext

The global application context, typically accessed as `ctx`.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `app` | `Vue.App` | The Vue application instance. |
| `routes` | `Object` | Access to application routes. |
| `ai` | `JsonApiClient` | The configured AI client for making API requests. |
| `fmt` | `Object` | Formatting utilities (e.g., date formats, currency). |
| `utils` | `Object` | General utility functions. |
| `state` | `Object` (Reactive) | Global reactive state object. |
| `events` | `EventBus` | Event bus for publishing and subscribing to global events. |
| `prefs` | `Object` (Reactive) | User preferences, persisted to local storage. |
| `layout` | `Object` (Reactive) | UI layout configuration (e.g., visibility of sidebars). |

### Global Helpers (Vue)

These properties are available globally in Vue templates and components:

*   `$ctx`: The `AppContext` instance.
*   `$prefs`: Alias for `ctx.prefs`.
*   `$state`: Alias for `ctx.state`.
*   `$layout`: Alias for `ctx.layout`.
*   `$ai`: Alias for `ctx.ai`.
*   `$fmt`: Alias for `ctx.fmt`.
*   `$utils`: Alias for `ctx.utils`.

### Methods

#### `scope(extensionName)`
Creates an extension-scoped context.
*   **extensionName**: `string` - Unique identifier for the extension.
*   **Returns**: `ExtensionScope`

#### `getPrefs()`
Returns the reactive preferences object.

#### `setPrefs(prefs)`
Updates the user preferences.
*   **prefs**: `Object` - Partial preferences object to merge.

#### `setState(state)`
Updates the global state.
*   **state**: `Object` - Partial state object to merge.

#### `setError(error, msg?)`
Sets a global error state.
*   **error**: `Error` - The error object.
*   **msg**: `string` (Optional) - Contextual message.

#### `clearError()`
Clears the global error state.

#### `toast(msg)`
Displays a toast notification.
*   **msg**: `string` - Message to display.

#### `to(route)`
Navigates to a specific route.
*   **route**: `string | Object` - The route path or route object.

### Layout & UI Methods

#### `setTopIcons(icons)`
Registers icons for the top header bar.
*   **icons**: `Object` - Map of icon definitions.

#### `setLeftIcons(icons)`
Registers icons for the left sidebar.
*   **icons**: `Object` - Map of icon definitions.

#### `setLeftTop(components)`
Adds components to the left side of the header bar.
*   **components**: `Object` - Map of `{ id: { component, isVisible?() } }`.

#### `setComposerTop(components)`
Adds compact controls to the chat prompt's chip row, after the project chip and before the model chip
(e.g. the agents extension's profile selector). Keep them chip-sized and open any popup upward, since the
prompt sits at the bottom of the screen.
*   **components**: `Object` - Map of `{ id: { component, isVisible?() } }`.

#### `component(name, component?)`
Registers or retrieves a global component.
*   **name**: `string` - Component name.
*   **component**: `Component` (Optional) - Vue component to register.

#### `components(components)`
Registers multiple components at once.
*   **components**: `Object` - Map of component names to Vue components.

#### `modals(modals)`
Registers modal components.
*   **modals**: `Object` - Map of modal names to components.

#### `openModal(name)`
Opens a registered modal.
*   **name**: `string` - Name of the modal to open.
*   **Returns**: `Component` - The modal component instance.

#### `closeModal(name)`
Closes a specific modal.
*   **name**: `string` - Name of the modal to close.

#### `toggleLayout(key, toggle?)`
Toggles visibility of a layout element.
*   **key**: `string` - Layout key (e.g., 'left', 'right').
*   **toggle**: `boolean` (Optional) - Force specific state.

#### `layoutVisible(key)`
Checks if a layout element is visible.
*   **key**: `string`
*   **Returns**: `boolean`

#### `toggleTop(name, toggle?)`
Toggles the active top view.
*   **name**: `string` - Name of the top view.
*   **toggle**: `boolean` (Optional) - Force specific state.

#### `togglePath(path, toggle?)`
Toggles navigation to a specific path, typically used for sidebar toggles.
*   **path**: `string` - URL path.
*   **toggle**: `boolean` (Optional) - Force specific state.

### HTTP Methods (Delegated to AI Client)

*   `getJson(url, options)`
*   `post(url, options)`
*   `postForm(url, options)`
*   `postJson(url, options)`

---

## ExtensionScope

Returned by `ctx.scope(name)`. Provides utilities scoped to a specific extension, including scoped local storage, error handling, and API endpoints.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `id` | `string` | Extension ID/Name. |
| `ctx` | `AppContext` | Reference to the parent context. |
| `baseUrl` | `string` | Base URL for extension API requests (`/api/ext/{id}`). |
| `storageKey` | `string` | Key prefix for local storage (`llms.{id}`). |
| `state` | `Object` (Reactive) | Local reactive state for the extension. |
| `prefs` | `Object` (Reactive) | Scoped preferences, persisted to `llms.{id}`. |

### Methods

#### `getPrefs()`
Returns the extension's reactive preferences.

#### `setPrefs(prefs)`
Updates and saves the extension's preferences.
*   **prefs**: `Object` - Partial object to merge.

#### `savePrefs()`
Force saves the current preferences to local storage.

#### `setError(e, msg?)`
Sets an error found within the extension. Automatically prefixes the message with the extension ID.
*   **e**: `Error`
*   **msg**: `string` (Optional)

#### `clearError()`
Clears the global error.

#### `toast(msg)`
Displays a toast notification.

### Scoped HTTP Methods

These methods automatically prepend the extension's `baseUrl` to the request URL.

#### `get(url, options)`
Makes a raw GET request relative to the extension's base URL.

#### `getJson(url, options)`
Makes a GET request expecting JSON relative to the extension's base URL.

#### `post(url, options)`
Makes a raw POST request relative to the extension's base URL.

#### `postJson(url, body)`
Makes a POST request sending JSON data.
*   **url**: `string`
*   **body**: `Object | FormData`

#### `postForm(url, options)`
Makes a POST request with form data.

#### `putJson(url, body)`
Makes a PUT request sending JSON data.

#### `patchJson(url, body)`
Makes a PATCH request sending JSON data.

#### `delete(url, options)`
Makes a DELETE request.

#### `deleteJson(url, options)`
Makes a DELETE request expecting JSON response.

#### `createJsonResult(res)`
Helper to create a standardized JSON result object from a response.

#### `createErrorResult(e)`
Helper to create a standardized error result object from an exception.
