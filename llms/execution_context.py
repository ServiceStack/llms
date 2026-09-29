"""Task-local workspace policy, inherited by asyncio tasks and asyncio.to_thread."""
from contextvars import ContextVar

workspace_scope = ContextVar("workspace_scope", default=None)
