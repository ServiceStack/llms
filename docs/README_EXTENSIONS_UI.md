# UI Extensions API

The UI Extensions API starts from `AppContext` which is available via the `ctx` singleton provider. An extension-scoped API can be created with `ext = ctx.scope(extensionName)`.

The `ctx` object provides access to the application state, routing, AI client, formatting utilities, and UI layout controls. It is globally available in Vue components as `$ctx` and can be imported in other modules.

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