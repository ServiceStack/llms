"""Extension hooks for detached, provider-native assistant replay content."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderReplayContext:
    """Filters mutate content in place; request identity and turn boundaries are read-only.

    No user message means all replay content belongs to the current turn. Tool results
    do not start a new user turn. Filters run synchronously, in registration order.
    """

    provider: Any
    model: str
    message_index: int
    last_user_message_index: int
    content: dict

    @property
    def is_current_turn(self):
        return self.message_index >= self.last_user_message_index


class GeminiReplayFilters:
    @staticmethod
    def exclude_completed_turn_tool_invocations(replay: ProviderReplayContext):
        """Opt-in workaround: omit earlier built-in steps, preserving other signed parts."""
        if replay.provider.sdk != "@ai-sdk/google" or replay.is_current_turn:
            return
        replay.content["parts"] = [
            part for part in replay.content.get("parts", []) if "toolCall" not in part and "toolResponse" not in part
        ]
