"""
Token counting for DeepForge — using tiktoken with DeepSeek-compatible encoding.

DeepSeek V3/V4 uses a BPE tokenizer similar to GPT-4's cl100k_base.
For accurate counting, we use tiktoken's cl100k_base encoding as a close approximation.
"""

from __future__ import annotations

from typing import Optional

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
except Exception:
    _enc = None


def count_tokens(text: str) -> int:
    """
    Count tokens in a text string.

    Uses tiktoken's cl100k_base encoding (close approximation for DeepSeek).
    Falls back to char/3 estimate if tiktoken is unavailable.
    """
    if _enc is not None:
        try:
            return len(_enc.encode(text))
        except Exception:
            pass
    # Fallback: ~3 characters per token
    return max(1, len(text) // 3)


def count_message_tokens(messages: list) -> int:
    """
    Count total tokens across a list of messages.

    Accounts for message formatting overhead (~4 tokens per message).
    """
    total = 0
    for msg in messages:
        # Message overhead
        total += 4
        # Content
        if hasattr(msg, "content") and msg.content:
            total += count_tokens(msg.content)
        elif isinstance(msg, dict) and msg.get("content"):
            total += count_tokens(msg["content"])
        # Tool calls
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                args_str = str(getattr(tc, "arguments", ""))
                total += count_tokens(args_str) + 8
        elif isinstance(msg, dict) and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                args_str = str(tc.get("arguments", ""))
                total += count_tokens(args_str) + 8
    return total


class TokenBudget:
    """
    Tracks token usage against a configured limit.

    Used by ContextWindow to decide when to compact.
    """

    def __init__(self, max_tokens: int = 1_000_000):
        self.max_tokens = max_tokens
        self._used: int = 0

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self._used)

    @property
    def usage_ratio(self) -> float:
        """0.0 to 1.0 — what fraction of the budget is used."""
        if self.max_tokens == 0:
            return 0.0
        return min(1.0, self._used / self.max_tokens)

    @property
    def needs_compaction(self) -> bool:
        """True when compaction should be triggered."""
        return self.usage_ratio >= 0.6

    @property
    def is_critical(self) -> bool:
        """True when context is dangerously full."""
        return self.usage_ratio >= 0.85

    def add(self, tokens: int) -> None:
        """Add consumed tokens to the budget."""
        self._used += tokens
        self._used = min(self._used, self.max_tokens * 2)  # Allow overshoot for tracking

    def reset(self) -> None:
        """Reset the budget after compaction."""
        self._used = 0

    def __repr__(self) -> str:
        return (
            f"TokenBudget({self._used:,}/{self.max_tokens:,} "
            f"= {self.usage_ratio:.1%}"
        )
