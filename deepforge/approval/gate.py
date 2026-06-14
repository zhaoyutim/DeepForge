"""
Approval Gate — mode × policy matrix for tool execution control.

Implements:
- Mode.PLAN + Policy.NEVER  → all writes blocked
- Mode.AGENT + Policy.SUGGEST → writes require approval
- Mode.YOLO + Policy.AUTO    → everything approved

The gate is consulted before every tool execution to determine
whether the tool can run silently, needs user approval, or is blocked.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from typing import Optional

from deepforge.config import ApprovalPolicy, Mode, config
from deepforge.tools.base import BaseTool
from deepforge.types import ToolCall


class GateDecision(str, Enum):
    """Result of the approval gate check."""
    ALLOW = "allow"        # Execute silently
    PROMPT = "prompt"      # Requires user approval
    BLOCK = "block"        # Execution blocked


@dataclass
class GateResult:
    """The gate's decision and rationale."""
    decision: GateDecision
    reason: str
    tool_name: str


class ApprovalGate:
    """
    Mode × Policy matrix for tool execution control.

    ┌──────────┬──────────┬──────────┬──────────┐
    │          │  AUTO    │ SUGGEST  │  NEVER   │
    ├──────────┼──────────┼──────────┼──────────┤
    │  AGENT   │ ALLOW    │ Read:ALLOW│ BLOCK   │
    │          │          │ Write:PROMPT│        │
    ├──────────┼──────────┼──────────┼──────────┤
    │  PLAN    │ BLOCK    │ BLOCK    │  BLOCK   │
    │          │ (write)  │ (write)  │ (all)    │
    ├──────────┼──────────┼──────────┼──────────┤
    │  YOLO    │ ALLOW    │ ALLOW    │  BLOCK   │
    └──────────┴──────────┴──────────┴──────────┘
    """

    def __init__(
        self,
        mode: Optional[Mode] = None,
        policy: Optional[ApprovalPolicy] = None,
    ):
        self.mode = mode or config.mode
        self.policy = policy or config.approval_policy

    # ── Gate Logic ───────────────────────────────────────────────

    def check(self, tool: BaseTool, tool_call: Optional[ToolCall] = None) -> GateResult:
        """
        Determine whether a tool call can proceed.

        Args:
            tool: The tool being invoked
            tool_call: The tool call (for context in error messages)

        Returns:
            GateResult with the decision
        """
        tool_name = tool_call.function_name if tool_call else tool.name

        # PLAN mode: all non-read or approval-required operations are blocked
        if self.mode == Mode.PLAN:
            if tool.is_write or tool.is_shell or tool.requires_approval:
                return GateResult(
                    decision=GateDecision.BLOCK,
                    reason="Plan mode: write/shell/approval-required operations are blocked.",
                    tool_name=tool_name,
                )
            return GateResult(
                decision=GateDecision.ALLOW,
                reason="Plan mode: read-only operations allowed.",
                tool_name=tool_name,
            )

        # NEVER policy: all writes blocked regardless of mode
        if self.policy == ApprovalPolicy.NEVER:
            if tool.is_write or tool.is_shell or tool.requires_approval:
                return GateResult(
                    decision=GateDecision.BLOCK,
                    reason="Approval policy NEVER: all write/shell/approval-required operations blocked.",
                    tool_name=tool_name,
                )
            return GateResult(
                decision=GateDecision.ALLOW,
                reason="Approval policy NEVER: read operations allowed.",
                tool_name=tool_name,
            )

        # YOLO mode or AUTO policy: everything allowed
        if self.mode == Mode.YOLO or self.policy == ApprovalPolicy.AUTO:
            return GateResult(
                decision=GateDecision.ALLOW,
                reason="YOLO mode / AUTO policy: all operations pre-approved.",
                tool_name=tool_name,
            )

        # AGENT mode + SUGGEST policy: reads allowed, writes prompt
        if tool.requires_approval:
            return GateResult(
                decision=GateDecision.PROMPT,
                reason=f"AGENT mode: '{tool_name}' is a write/shell operation and requires approval.",
                tool_name=tool_name,
            )
        return GateResult(
            decision=GateDecision.ALLOW,
            reason=f"AGENT mode: '{tool_name}' is read-only.",
            tool_name=tool_name,
        )

    def check_batch(self, tools_and_calls: list[tuple[BaseTool, ToolCall]]) -> list[GateResult]:
        """
        Check multiple tool calls at once.

        Returns a list of GateResults in the same order.
        """
        return [self.check(tool, tc) for tool, tc in tools_and_calls]

    def any_blocked(self, results: list[GateResult]) -> bool:
        """Check if any gate result is BLOCK."""
        return any(r.decision == GateDecision.BLOCK for r in results)

    def any_requires_approval(self, results: list[GateResult]) -> bool:
        """Check if any gate result requires user approval."""
        return any(r.decision == GateDecision.PROMPT for r in results)

    def filter_allowed(self, results: list[GateResult]) -> list[int]:
        """Return indices of ALLOW decisions."""
        return [
            i for i, r in enumerate(results)
            if r.decision == GateDecision.ALLOW
        ]

    # ── Display Helpers ──────────────────────────────────────────

    def format_batch_summary(self, results: list[GateResult]) -> str:
        """Format a human-readable summary of gate decisions."""
        allowed = sum(1 for r in results if r.decision == GateDecision.ALLOW)
        prompted = sum(1 for r in results if r.decision == GateDecision.PROMPT)
        blocked = sum(1 for r in results if r.decision == GateDecision.BLOCK)

        parts = [f"{len(results)} tool(s): {allowed} allowed"]
        if prompted:
            parts.append(f"{prompted} need approval")
        if blocked:
            parts.append(f"{blocked} blocked")

        return ", ".join(parts)


# ── Convenience function ─────────────────────────────────────────

def get_gate(mode: Optional[Mode] = None, policy: Optional[ApprovalPolicy] = None) -> ApprovalGate:
    """Get an approval gate with the given mode/policy."""
    return ApprovalGate(mode=mode, policy=policy)
