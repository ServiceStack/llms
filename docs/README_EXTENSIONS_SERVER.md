# Server Extensions API

Server Extensions allow you to extend the functionality of the LLM Server by registering new providers, UI extensions, HTTP routes, and hooking into the chat pipeline.
The Public API surface is exposed via the `ExtensionContext` class which provides access to the Server's functionality.

## Logging & Debugging

Methods for logging information to the console with the extension name prefix.

### `log(message)`
Log a message to stdout if verbose mode is enabled.
- **message**: `str` - The message to log.

### `log_json(obj)`
Log a JSON object to stdout if verbose mode is enabled.
- **obj**: `Any` - The object to serialize and log.

### `dbg(message)`
Log a debug message to stdout if debug mode is enabled.
- **message**: `str` - The debug message.

### `err(message, e)`
Log an error message and exception trace.
- **message**: `str` - The error description.
- **e**: `Exception` - The exception object.

## Registration & Configuration

Methods to register various extension components.

### `add_provider(provider)`
Register a new LLM provider.
- **provider**: `class` - The provider class to register.

### `register_ui_extension(index)`
Register a UI extension that will be loaded in the browser.
- **index**: `str` - Relative path to the index file (e.g. "index.html" or "app.mjs") within the extension directory.

### `register_tool(func, tool_def=None)`
Register a function as a tool that can be used by LLMs.
- **func**: `callable` - The Python function to register.
- **tool_def**: `dict` (Optional) - Manual tool definition. If None, it's generated from the function signature.

### `add_static_files(ext_dir)`
Serve static files from a directory.
- **ext_dir**: `str` - Absolute path to the directory containing static files.

### `add_importmaps(dict)`
Add entries to the browser's import map, allowing you to map package names to URLs.
- **dict**: `dict` - A dictionary of import map entries (e.g. `{"vue": "/ui/lib/vue.mjs"}`).

### `add_index_header(html)`
Inject HTML into the `<head>` section of the main index page.
- **html**: `str` - The HTML string to inject.

### `add_index_footer(html)`
Inject HTML into the end of the `<body>` section of the main index page.
- **html**: `str` - The HTML string to inject.

### `register_shutdown_handler(handler)`
Register a callback to be called when the server shuts down.
- **handler**: `callable` - The function to call on shutdown.

## HTTP Routes

Register custom HTTP endpoints. All paths are prefixed with `/ext/{extension_name}`.

### `add_get(path, handler, **kwargs)`
Register a GET route.
- **path**: `str` - The sub-path for the route.
- **handler**: `callable` - Async function taking `request` and returning `web.Response`.

### `add_post(path, handler, **kwargs)`
Register a POST route.
- **path**: `str` - The sub-path for the route.
- **handler**: `callable` - Async function taking `request` and returning `web.Response`.

### `add_put(path, handler, **kwargs)`
Register a PUT route.

### `add_delete(path, handler, **kwargs)`
Register a DELETE route.

### `add_patch(path, handler, **kwargs)`
Register a PATCH route.

## Chat & LLM Interaction

Methods to interact with the LLM chat pipeline.

### `chat_request(template=None, text=None, model=None, system_prompt=None)`
Create a new chat request object, typically to be sent to `chat_completion`.
- **template**: `str` (Optional) - Template ID to use.
- **text**: `str` (Optional) - User message text.
- **model**: `str` (Optional) - Model identifier.
- **system_prompt**: `str` (Optional) - System prompt to use.

### `chat_completion(chat, context=None)`
Execute a chat completion request against the configured LLM.
- **chat**: `dict` - The chat request object.
- **context**: `dict` (Optional) - execution context.
- **Returns**: `ChatResponse` - The LLM's response.

### `chat_to_prompt(chat)`
Convert a chat object to a prompt string (depends on configured prompts).

### `chat_to_system_prompt(chat)`
Extract or generate the system prompt from a chat object.

### `chat_response_to_message(response)`
Convert a provider's raw response to a standard message format.

### `last_user_prompt(chat)`
Get the last user message from a chat history.

## Filters

Hooks to intercept and modify the chat lifecycle.

### `register_chat_request_filter(handler)`
Register a filter to modify chat requests before they are processed.
- **handler**: `callable(request)`

### `register_chat_tool_filter(handler)`
Register a filter to modify or restrict tools available to the LLM.
- **handler**: `callable(tools, context)`

### `register_chat_response_filter(handler)`
Register a filter to modify chat responses before they are returned to the client.
- **handler**: `callable(response, context)`

### `register_chat_error_filter(handler)`
Register a filter to handle or transform exceptions during chat.
- **handler**: `callable(error, context)`

### `register_cache_saved_filter(handler)`
Register a filter called when a response is saved to cache.
- **handler**: `callable(context)`

## Authentication & User Context

Access user session and authentication information.

### `check_auth(request)`
Check if the request is authenticated.
- **Returns**: `(bool, dict)` - Tuple of `(is_authenticated, user_data)`.

### `get_session(request)`
Get the session data for the current request.
- **Returns**: `dict` or `None`.

### `get_username(request)`
Get the authenticated username from the request.
- **Returns**: `str` or `None`.

### `get_user_path(username=None)`
Get the absolute path to a user's data directory.
- **username**: `str` (Optional) - Specific username, otherwise uses current context or default.

## Files & Storage

Utilities for file handling and caching.

### `text_from_file(path)`
Read text content from a file.

### `save_image_to_cache(base64_data, filename, image_info)`
Save a base64 encoded image to the media cache.

### `save_bytes_to_cache(bytes_data, filename, file_info)`
Save raw bytes to the media cache.

### `get_cache_path(path="")`
Get the absolute path to the global cache directory.

### `to_file_info(chat, info=None, response=None)`
Helper to create file metadata info from a chat context.

### `cache_message_inline_data(message)`
Process a message to extract inline data (like images) to cache and replace with URLs.

## Utilities

### `get_config()`
Get the global server configuration object.

### `get_providers()`
Get a list of all registered provider handlers.

### `get_provider(name)`
Get a specific provider instance by name.

### `to_content(result)`
Convert a result object (e.g. from a tool) into a standard content string format.

### `error_response(e, stacktrace=False)`
Create a standardized error HTTP response from an exception.

### `should_cancel_thread(context)`
Check if the current processing thread has been flagged for cancellation.