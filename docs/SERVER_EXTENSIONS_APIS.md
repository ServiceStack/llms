# Server Extensions APIs

This document covers the public APIs available to server-side extensions via the `ExtensionContext` and `AppExtensions` classes.

## Overview

When creating a server extension, you work with an `ExtensionContext` instance that provides access to all extension capabilities. The `ExtensionContext` is passed to your extension's `init(ctx)` function and serves as the primary interface for registering handlers, routes, tools, and accessing server functionality.

```python
def init(ctx: ExtensionContext):
    # Your extension initialization code here
    ctx.register_tool(my_tool_function)
    ctx.add_get("status", handle_status)
```

---

## ExtensionContext

The `ExtensionContext` class is the main interface for extensions. It provides access to the extension's configuration, logging, and registration methods.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `app` | `AppExtensions` | Reference to the parent application extensions manager |
| `cli_args` | `argparse.Namespace` | Command-line arguments passed to the server |
| `extra_args` | `Dict[str, Any]` | Additional arguments from extensions |
| `error_auth_required` | `Dict[str, Any]` | Pre-built authentication required error response |
| `path` | `str` | File path of the extension |
| `name` | `str` | Name of the extension (derived from filename) |
| `ext_prefix` | `str` | URL prefix for extension routes (e.g., `/ext/myext`) |
| `MOCK` | `bool` | Whether mock mode is enabled |
| `MOCK_DIR` | `str` | Directory for mock data files |
| `debug` | `bool` | Whether debug mode is enabled |
| `verbose` | `bool` | Whether verbose logging is enabled |
| `aspect_ratios` | `Dict[str, str]` | Available image aspect ratios (e.g., `"1:1": "1024×1024"`) |
| `request_args` | `Dict[str, type]` | Supported chat request arguments with their types |
| `disabled` | `bool` | Whether the extension is disabled |

---

### Logging Methods

#### `log(message: Any) -> Any`
Log a message when verbose mode is enabled. Returns the message for chaining.

```python
ctx.log("Processing request...")
```

#### `log_json(obj: Any) -> Any`
Log an object as formatted JSON when verbose mode is enabled. Returns the object for chaining.

```python
ctx.log_json({"status": "ok", "count": 42})
```

#### `dbg(message: Any)`
Log a debug message when debug mode is enabled.

```python
ctx.dbg("Entering handler with params: ...")
```

#### `err(message: str, e: Exception)`
Log an error with exception details. Prints stack trace in verbose mode.

```python
try:
    process_data()
except Exception as e:
    ctx.err("Failed to process data", e)
```

---

### Route Registration

Routes are automatically prefixed with `/ext/{extension_name}`.

#### `add_get(path: str, handler: Callable, **kwargs: Any)`
Register a GET route handler.

```python
async def handle_status(request):
    return web.json_response({"status": "ok"})

ctx.add_get("status", handle_status)  # Available at /ext/myext/status
```

#### `add_post(path: str, handler: Callable, **kwargs: Any)`
Register a POST route handler.

```python
async def handle_create(request):
    data = await request.json()
    return web.json_response({"created": True})

ctx.add_post("create", handle_create)
```

#### `add_put(path: str, handler: Callable, **kwargs: Any)`
Register a PUT route handler.

#### `add_delete(path: str, handler: Callable, **kwargs: Any)`
Register a DELETE route handler.

#### `add_patch(path: str, handler: Callable, **kwargs: Any)`
Register a PATCH route handler.

#### `add_static_files(ext_dir: str)`
Serve static files from a directory under the extension's URL prefix.

```python
# Serve files from ./ui directory at /ext/myext/*
ext_dir = os.path.join(os.path.dirname(__file__), "ui")
ctx.add_static_files(ext_dir)
```

#### `web_path(method: str, path: str) -> str`
Get the full URL path for a route (internal helper).

---

### Filter Registration

Filters intercept and can modify requests/responses at various stages.

#### `register_chat_request_filter(handler: Callable)`
Register a filter that runs before chat requests are processed.

```python
async def filter_request(chat: Dict, context: Dict):
    # Modify chat request before processing
    chat["metadata"] = {"source": "extension"}

ctx.register_chat_request_filter(filter_request)
```

#### `register_chat_tool_filter(handler: Callable)`
Register a filter that runs when tools are invoked.

```python
async def on_tool_call(chat: Dict, context: Dict):
    ctx.log(f"Tool called in thread: {context.get('threadId')}")

ctx.register_chat_tool_filter(on_tool_call)
```

#### `register_chat_response_filter(handler: Callable)`
Register a filter that runs after chat responses are generated.

```python
async def filter_response(response: Dict, context: Dict):
    # Modify or log response
    pass

ctx.register_chat_response_filter(filter_response)
```

#### `register_chat_error_filter(handler: Callable)`
Register a filter that runs when chat errors occur.

```python
async def on_error(e: Exception, context: Dict):
    ctx.log(f"Error: {e}, Stack: {context.get('stackTrace')}")

ctx.register_chat_error_filter(on_error)
```

#### `register_cache_saved_filter(handler: Callable)`
Register a filter that runs when responses are saved to cache.

```python
def on_cache_saved(context: Dict):
    ctx.log(f"Cached: {context['url']}")

ctx.register_cache_saved_filter(on_cache_saved)
```

#### `register_shutdown_handler(handler: Callable)`
Register a handler to run when the server shuts down.

```python
def cleanup():
    ctx.log("Extension shutting down...")

ctx.register_shutdown_handler(cleanup)
```

---

### Tool Registration

#### `register_tool(func: Callable, tool_def: Optional[Dict] = None, group: Optional[str] = None)`
Register a tool function that LLMs can invoke.

```python
def search_database(query: str, limit: int = 10) -> Dict[str, Any]:
    """Search the database for matching records.
    
    Args:
        query: Search query string
        limit: Maximum number of results to return
    
    Returns:
        Dictionary containing search results
    """
    results = do_search(query, limit)
    return {"results": results}

ctx.register_tool(search_database, group="database")
```

- If `tool_def` is not provided, it's automatically generated from the function signature and docstring
- `group` categorizes the tool for UI display (defaults to `"custom"`)

#### `get_tool_definition(name: str) -> Optional[Dict[str, Any]]`
Retrieve the tool definition for a registered tool.

```python
tool_def = ctx.get_tool_definition("search_database")
```

#### `sanitize_tool_def(tool_def: Dict[str, Any]) -> Dict[str, Any]`
Process a tool definition to inline `$defs` references.

---

### Tool Execution

#### `async exec_tool(name: str, args: Dict[str, Any]) -> Tuple[Optional[str], List[Dict[str, Any]]]`
Execute a registered tool by name.

```python
error, results = await ctx.exec_tool("search_database", {"query": "test"})
if error:
    ctx.err("Tool execution failed", Exception(error))
```

#### `tool_result(result: Any, function_name: Optional[str] = None, function_args: Optional[Dict] = None) -> Dict[str, Any]`
Format a tool execution result for return to the LLM.

#### `tool_result_part(result: Dict, function_name: Optional[str] = None, function_args: Optional[Dict] = None) -> Dict[str, Any]`
Format a partial tool result.

#### `to_content(result: Any) -> str`
Convert a result to string content.

---

### Chat Utilities

#### `chat_request(template: Optional[str] = None, text: Optional[str] = None, model: Optional[str] = None, system_prompt: Optional[str] = None) -> Dict[str, Any]`
Create a chat request object.

```python
chat = ctx.chat_request(
    text="Summarize this document",
    model="gpt-4o",
    system_prompt="You are a helpful assistant"
)
```

#### `async chat_completion(chat: Dict[str, Any], context: Optional[Dict] = None) -> Any`
Send a chat completion request.

```python
chat = ctx.chat_request(text="Hello, world!")
response = await ctx.chat_completion(chat)
```

#### `create_chat_with_tools(chat: Dict[str, Any], use_tools: str = "all") -> Dict[str, Any]`
Create a chat request with tools injected.

```python
chat = ctx.chat_request(text="Search for recent news")
chat_with_tools = ctx.create_chat_with_tools(chat, use_tools="search_web,fetch_page")
```

#### `chat_to_prompt(chat: Dict[str, Any]) -> str`
Extract the user prompt from a chat object.

#### `chat_to_system_prompt(chat: Dict[str, Any]) -> str`
Extract the system prompt from a chat object.

#### `last_user_prompt(chat: Dict[str, Any]) -> str`
Get the last user message from a chat.

#### `chat_response_to_message(response: Dict[str, Any]) -> Dict[str, Any]`
Convert a chat response to a message format.

#### `chat_to_aspect_ratio(chat: Dict[str, Any]) -> str`
Extract aspect ratio from chat request.

---

### File Utilities

#### `text_from_file(path: str) -> str`
Read text content from a file.

```python
content = ctx.text_from_file("/path/to/file.txt")
```

#### `json_from_file(path: str) -> Any`
Read and parse JSON from a file.

```python
data = ctx.json_from_file("/path/to/config.json")
```

#### `download_file(url: str) -> Tuple[bytes, Dict[str, Any]]`
Download a file from a URL. Returns bytes and metadata.

#### `session_download_file(session: aiohttp.ClientSession, url: str) -> Tuple[bytes, Dict[str, Any]]`
Download a file using an existing aiohttp session.

#### `read_binary_file(url: str) -> Tuple[bytes, Dict[str, Any]]`
Read binary file content from a URL or path.

---

### Cache Utilities

#### `get_cache_path(path: str = "") -> str`
Get the full path to a cache location.

```python
cache_file = ctx.get_cache_path("my_extension/data.json")
```

#### `save_image_to_cache(base64_data: Union[str, bytes], filename: str, image_info: Dict[str, Any], ignore_info: bool = False) -> Tuple[str, Optional[Dict[str, Any]]]`
Save image data to the cache. Returns the cache path and info.

```python
path, info = ctx.save_image_to_cache(b64_data, "output.png", {"prompt": "..."})
```

#### `save_bytes_to_cache(bytes_data: Union[str, bytes], filename: str, file_info: Optional[Dict[str, Any]]) -> Tuple[str, Optional[Dict[str, Any]]]`
Save binary data to the cache.

#### `cache_message_inline_data(message: Dict[str, Any])`
Cache inline data (e.g., base64 images) from a message.

---

### Provider Access

#### `get_providers() -> Dict[str, Any]`
Get all registered LLM providers.

```python
providers = ctx.get_providers()
for name, provider in providers.items():
    ctx.log(f"Provider: {name}")
```

#### `get_provider(name: str) -> Optional[Any]`
Get a specific provider by name.

```python
openai = ctx.get_provider("openai")
```

#### `add_provider(provider: Any)`
Register a new LLM provider class.

```python
from llms.main import OpenAiCompatible

class MyProvider(OpenAiCompatible):
    name = "my-provider"
    # ...

ctx.add_provider(MyProvider)
```

---

### Authentication & Sessions

#### `check_auth(request: web.Request) -> Tuple[bool, Optional[Dict[str, Any]]]`
Check if a request is authenticated. Returns `(is_authenticated, user_data)`.

```python
async def protected_route(request):
    is_auth, user = ctx.check_auth(request)
    if not is_auth:
        return ctx.error_auth_required
    return web.json_response({"user": user})
```

#### `get_session(request: web.Request) -> Optional[Dict[str, Any]]`
Get the session data for a request.

```python
session = ctx.get_session(request)
if session:
    ctx.log(f"User: {session.get('userName')}")
```

#### `get_username(request: web.Request) -> Optional[str]`
Get the username from a request's session.

#### `get_user_path(username: Optional[str] = None) -> str`
Get the filesystem path for user-specific data.

```python
user_dir = ctx.get_user_path("john")  # ~/.llms/user/john
```

#### `context_to_username(context: Optional[Dict[str, Any]]) -> Optional[str]`
Extract username from a context dictionary containing a request.

---

### Configuration & Utilities

#### `get_config() -> Optional[Dict[str, Any]]`
Get the current server configuration.

```python
config = ctx.get_config()
api_key = config.get("auth", {}).get("api_key")
```

#### `get_file_mime_type(filename: str) -> str`
Get the MIME type for a filename.

```python
mime = ctx.get_file_mime_type("image.png")  # "image/png"
```

#### `to_file_info(chat: Dict[str, Any], info: Optional[Dict] = None, response: Optional[Dict] = None) -> Dict[str, Any]`
Create file info metadata from chat/response data.

#### `group_resources(resources: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]`
Group a list of resources by category.

#### `should_cancel_thread(context: Dict[str, Any]) -> bool`
Check if the current thread/request should be cancelled.

---

### Error Handling

#### `error_message(e: Exception) -> str`
Extract a user-friendly error message from an exception.

```python
try:
    risky_operation()
except Exception as e:
    msg = ctx.error_message(e)
    return web.json_response({"error": msg}, status=500)
```

#### `error_response(e: Exception, stacktrace: bool = False) -> Dict[str, Any]`
Create an error response dictionary from an exception.

```python
try:
    process()
except Exception as e:
    return web.json_response(ctx.error_response(e, stacktrace=True), status=500)
```

---

### UI Registration

#### `register_ui_extension(index: str)`
Register a UI extension entry point.

```python
ctx.register_ui_extension("index.mjs")  # Registers /ext/myext/index.mjs
```

#### `add_importmaps(dict: Dict[str, str])`
Add JavaScript import map entries.

```python
ctx.add_importmaps({
    "my-lib": "/ext/myext/lib/my-lib.mjs"
})
```

#### `add_index_header(html: str)`
Add HTML to the main page header.

```python
ctx.add_index_header('<link rel="stylesheet" href="/ext/myext/styles.css">')
```

#### `add_index_footer(html: str)`
Add HTML to the main page footer.

```python
ctx.add_index_footer('<script src="/ext/myext/analytics.js"></script>')
```

---

## AppExtensions

The `AppExtensions` class manages all registered extensions and provides shared state. While extensions primarily interact through `ExtensionContext`, some `AppExtensions` properties are accessible.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `cli_args` | `argparse.Namespace` | Command-line arguments |
| `extra_args` | `Dict[str, Any]` | Additional extension arguments |
| `config` | `Dict[str, Any]` | Server configuration |
| `auth_enabled` | `bool` | Whether authentication is enabled |
| `ui_extensions` | `List[Dict]` | Registered UI extensions |
| `tools` | `Dict[str, Callable]` | Registered tool functions by name |
| `tool_definitions` | `List[Dict]` | Tool definitions for LLM consumption |
| `tool_groups` | `Dict[str, List[str]]` | Tool names grouped by category |
| `all_providers` | `List[type]` | All registered provider classes |
| `import_maps` | `Dict[str, str]` | JavaScript import map entries |
| `index_headers` | `List[str]` | HTML headers for main page |
| `index_footers` | `List[str]` | HTML footers for main page |
| `aspect_ratios` | `Dict[str, str]` | Image aspect ratio mappings |
| `request_args` | `Dict[str, type]` | Supported request argument types |

### Default Request Arguments

The `request_args` dictionary defines supported chat request parameters:

```python
{
    "image_config": dict,        # e.g., {"aspect_ratio": "1:1"}
    "temperature": float,        # e.g., 0.7
    "max_completion_tokens": int, # e.g., 2048
    "seed": int,                 # e.g., 42
    "top_p": float,              # e.g., 0.9
    "frequency_penalty": float,  # e.g., 0.5
    "presence_penalty": float,   # e.g., 0.5
    "stop": list,                # e.g., ["Stop"]
    "reasoning_effort": str,     # e.g., "minimal", "low", "medium", "high"
    "verbosity": str,            # e.g., "low", "medium", "high"
    "service_tier": str,         # e.g., "auto", "default"
    "top_logprobs": int,
    "safety_identifier": str,
    "store": bool,
    "enable_thinking": bool,
}
```

### Default Aspect Ratios

```python
{
    "1:1": "1024×1024",
    "2:3": "832×1248",
    "3:2": "1248×832",
    "3:4": "864×1184",
    "4:3": "1184×864",
    "4:5": "896×1152",
    "5:4": "1152×896",
    "9:16": "768×1344",
    "16:9": "1344×768",
    "21:9": "1536×672",
}
```

### Default Import Maps

```python
{
    "vue-prod": "/ui/lib/vue.min.mjs",
    "vue": "/ui/lib/vue.mjs",
    "vue-router": "/ui/lib/vue-router.min.mjs",
    "@servicestack/client": "/ui/lib/servicestack-client.mjs",
    "@servicestack/vue": "/ui/lib/servicestack-vue.mjs",
    "idb": "/ui/lib/idb.min.mjs",
    "marked": "/ui/lib/marked.min.mjs",
    "highlight.js": "/ui/lib/highlight.min.mjs",
    "chart.js": "/ui/lib/chart.js",
    "color.js": "/ui/lib/color.js",
    "ctx.mjs": "/ui/ctx.mjs",
}
```

---

## Example Extension

Here's a complete example demonstrating common extension patterns:

```python
import os
from aiohttp import web

def init(ctx):
    """Initialize the example extension."""
    
    # Register a custom tool
    def greet_user(name: str, formal: bool = False) -> str:
        """Greet a user by name.
        
        Args:
            name: The user's name
            formal: Whether to use formal greeting
        
        Returns:
            A greeting message
        """
        if formal:
            return f"Good day, {name}. How may I assist you?"
        return f"Hey {name}! What's up?"
    
    ctx.register_tool(greet_user, group="social")
    
    # Register API routes
    async def get_status(request):
        is_auth, user = ctx.check_auth(request)
        return web.json_response({
            "extension": ctx.name,
            "authenticated": is_auth,
            "user": user.get("userName") if user else None
        })
    ctx.add_get("status", get_status)
    
    async def create_item(request):
        is_auth, _ = ctx.check_auth(request)
        if not is_auth:
            return ctx.error_auth_required
        
        try:
            data = await request.json()
            # Process data...
            return web.json_response({"success": True, "id": "123"})
        except Exception as e:
            return web.json_response(ctx.error_response(e), status=500)
    ctx.add_post("items", create_item)
    
    # Register filters
    async def log_requests(chat, context):
        ctx.log(f"Chat request for thread: {context.get('threadId')}")
    ctx.register_chat_request_filter(log_requests)
    
    # Cleanup on shutdown
    def cleanup():
        ctx.log("Extension shutting down, cleaning up...")
    ctx.register_shutdown_handler(cleanup)
    
    ctx.log("Example extension initialized!")
```
