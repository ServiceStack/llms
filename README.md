# llms.py

Lightweight CLI, API and ChatGPT-like alternative to Open WebUI for accessing multiple LLMs, entirely offline, with all data kept private in browser storage.

[llmspy.org](https://llmspy.org)

[![](https://github.com/ServiceStack/llmspy.org/blob/main/public/img/llmspy-splash.webp?raw=true)](https://llmspy.org)

GitHub: [llmspy.org](https://github.com/ServiceStack/llmspy.org)

## MCP client

Connect conversations and durable agents to approved remote MCP tools, with per-user connections,
OAuth, editable approvals, and recovery without replaying uncertain calls. The Python and C# AI.Chat
implementations share the same endpoints and UI. See [MCP client configuration](llms/extensions/mcp_client/README.md).

Projects and conversations share through independently enabled `share_static` and `share_llmspy`
extensions. See [sharing options](docs/SHARING.md) for configuration and extension registration.

### Gemini Sync repairs

Sync Store retries upload and metadata errors from the local cache and removes duplicate remote
copies belonging to the same document. It preserves separate source documents with identical content
and keeps a healthy fallback until a replacement succeeds. Cleanup failures remain durable and appear
under **Repair Errors** for retry on the next Sync. Documents being uploaded are left in progress;
missing source files and provider/billing failures are reported rather than silently discarded.
