"""
Context Window Manager — tracks token usage, enforces limits, and triggers compaction.

Implements:
- TokenBudget tracking against the 1M-token window
- Prefix cache awareness (append, don't rewrite)
- Compaction trigger at 60% usage
- Critical warning at 85% usage
- Tier-based context layering (stable first, volatile last)
"""

from __future__ import annotations

import time
from typing import Optional

from deepforge.config import config
from deepforge.models.tokenizer import TokenBudget, count_tokens
from deepforge.types import Turn


class ContextWindow:
    """
    Manages the conversation context window.

    Responsibilities:
    1. Track token usage across turns
    2. Detect when compaction is needed (60% threshold)
    3. Provide context pressure warnings
    4. Maintain prefix-cache awareness

    The context is structured in tiers (stable → volatile):
      Tier 1: System prompt (Constitution + instructions)
      Tier 2: Recent turns (last N turns)
      Tier 3: Current turn (in-flight)
    """

    def __init__(
        self,
        max_tokens: Optional[int] = None,
        compaction_threshold: Optional[float] = None,
    ):
        self.max_tokens = max_tokens or config.max_context_tokens
        self.compaction_threshold = compaction_threshold or config.compaction_threshold
        self.budget = TokenBudget(self.max_tokens)

        # State
        self.turns: list[Turn] = []
        self.system_prompt: Optional[str] = None
        self.system_prompt_tokens: int = 0
        self.total_turns: int = 0
        self.last_compaction_at: Optional[float] = None

    # ── Token Accounting ─────────────────────────────────────────

    def set_system_prompt(self, prompt: str) -> None:
        """Set the system prompt and account for its tokens."""
        self.system_prompt = prompt
        self.system_prompt_tokens = count_tokens(prompt)
        self.budget.add(self.system_prompt_tokens)

    def add_turn(self, turn: Turn) -> None:
        """Add a turn to the context and update the budget."""
        # Count tokens in the turn
        turn_tokens = self._estimate_turn_tokens(turn)
        turn.thinking_tokens = turn_tokens  # Store for reference

        self.turns.append(turn)
        self.total_turns += 1
        self.budget.add(turn_tokens)

    def _estimate_turn_tokens(self, turn: Turn) -> int:
        """Estimate tokens for a turn (user + assistant + tool results)."""
        tokens = 0
        # User message
        if turn.user_message.content:
            tokens += count_tokens(turn.user_message.content)
        # Assistant response
        if turn.assistant_message and turn.assistant_message.content:
            tokens += count_tokens(turn.assistant_message.content)
        # Tool results
        for result in turn.tool_results:
            tokens += count_tokens(result.content)
        # Tool calls in assistant message
        if turn.assistant_message and turn.assistant_message.tool_calls:
            for tc in turn.assistant_message.tool_calls:
                tokens += count_tokens(str(tc.arguments)) + 8

        # Message formatting overhead
        tokens += 16  # ~4 tokens per message role
        return tokens

    # ── Compaction Decisions ─────────────────────────────────────

    @property
    def usage_ratio(self) -> float:
        """0.0 to 1.0 — current context window fill level."""
        return self.budget.usage_ratio

    @property
    def needs_compaction(self) -> bool:
        """
        True when compaction should be triggered.

        Triggers at 60% usage by default, as compaction itself
        costs tokens and invalidates the prefix cache.
        """
        return self.budget.needs_compaction

    @property
    def is_critical(self) -> bool:
        """True when context is dangerously full (85%+)."""
        return self.budget.is_critical

    @property
    def context_pressure(self) -> str:
        """
        Human-readable context pressure indicator.

        Returns:
            "low" (< 40%), "medium" (40-60%), "high" (60-85%), "critical" (> 85%)
        """
        ratio = self.usage_ratio
        if ratio < 0.4:
            return "low"
        elif ratio < 0.6:
            return "medium"
        elif ratio < 0.85:
            return "high"
        return "critical"

    @property
    def pressure_emoji(self) -> str:
        """Emoji indicator for context pressure."""
        return {
            "low": "🟢",
            "medium": "🟡",
            "high": "🟠",
            "critical": "🔴",
        }.get(self.context_pressure, "⚪")

    # ── Compaction ──────────────────────────────────────────────

    def compact(self) -> dict:
        """
        Compact the conversation history into a summary.

        Keeps:
        - System prompt (Tier 1)
        - Last 2 turns (most recent context)
        - Compaction relay summary of earlier turns

        Returns:
            Summary dict for the compaction relay.
        """
        if len(self.turns) <= 3:
            # Not enough turns to compact
            return {"compacted": False, "reason": "Too few turns to compact"}

        # Keep the last 2 turns
        recent_turns = self.turns[-2:]
        early_turns = self.turns[:-2]

        # Generate summary
        summary = self._generate_compaction_summary(early_turns)

        # Reset and rebuild
        early_tokens = sum(self._estimate_turn_tokens(t) for t in early_turns)
        recent_tokens = sum(self._estimate_turn_tokens(t) for t in recent_turns)
        summary_tokens = count_tokens(summary)

        # Update budget
        self.budget.reset()
        self.budget.add(self.system_prompt_tokens)
        self.budget.add(summary_tokens)
        self.budget.add(recent_tokens)

        # Replace turns
        self.turns = recent_turns
        self.last_compaction_at = time.time()

        return {
            "compacted": True,
            "turns_compacted": len(early_turns),
            "tokens_freed": early_tokens - summary_tokens,
            "summary_tokens": summary_tokens,
            "summary": summary,
        }

    def _generate_compaction_summary(self, turns: list[Turn]) -> str:
        """Generate a structured summary of compacted turns."""
        user_messages = [
            t.user_message.content
            for t in turns
            if t.user_message.content
        ]

        # Simple summary: extract first sentences of user messages
        summaries = []
        for msg in user_messages[:5]:  # Max 5 messages in summary
            first_sentence = msg.split(".")[0].strip()
            if len(first_sentence) > 100:
                first_sentence = first_sentence[:97] + "..."
            summaries.append(f"- {first_sentence}")

        header = (
            "[Compaction Relay]\n"
            f"The previous {len(turns)} turns have been compacted.\n"
            f"Key topics discussed:\n"
        )
        return header + "\n".join(summaries) if summaries else header + "(no user messages)"

    # ── Prefix Cache Awareness ──────────────────────────────────

    def get_cache_stability(self, new_content: str) -> float:
        """
        Estimate prefix cache stability when adding new content.

        The prefix cache is most stable when we append (don't rewrite).
        Returns 0.0 (cache broken) to 1.0 (full cache hit expected).

        This is a heuristic based on the last turn boundary.
        """
        # In practice, new content appended at the end preserves the cache
        # Rewriting earlier content breaks it
        if self.turns:
            # If we have at least one turn, appending preserves ~90% of the cache
            return 0.9
        return 0.0  # First turn, no cache to preserve

    def append_preserves_cache(self) -> bool:
        """True if appending a new turn will preserve most of the prefix cache."""
        return len(self.turns) > 0

    # ── Stats ────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return context window statistics."""
        return {
            "used_tokens": self.budget.used,
            "max_tokens": self.max_tokens,
            "usage_ratio": f"{self.usage_ratio:.1%}",
            "pressure": self.context_pressure,
            "total_turns": self.total_turns,
            "active_turns": len(self.turns),
            "needs_compaction": self.needs_compaction,
            "is_critical": self.is_critical,
            "system_prompt_tokens": self.system_prompt_tokens,
            "last_compaction": self.last_compaction_at,
        }

    def __repr__(self) -> str:
        return (
            f"<ContextWindow {self.budget.used:,}/{self.max_tokens:,} "
            f"({self.context_pressure}) {self.pressure_emoji}>"
        )
