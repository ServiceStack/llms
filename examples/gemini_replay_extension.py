"""Optional workaround for missing follow-up File Search citations (discussion #163).

Save as ~/.llms/extensions/gemini_replay/__init__.py to enable. No UI or config
changes are required. This differs from Google's documented full-history replay
and cannot guarantee citations; stored history and current tool loops stay intact.
"""

from llms.provider_replay import GeminiReplayFilters


def install(ctx):
    ctx.register_provider_replay_filter(GeminiReplayFilters.exclude_completed_turn_tool_invocations)


__install__ = install
