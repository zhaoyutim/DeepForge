"""
Constitution of CodeX — the non-negotiable rule engine.

Implements Article VII: The Hierarchy of Law.
Conflicts between directives are resolved in this priority order:
  Tier 1: Constitution (safety, truth, user agency)
  Tier 2: User's current request (highest directive below Constitution)
  Tier 3: Mode permissions & approval policies (Statutes)
  Tier 4: Best-practice patterns (Regulations)
  Tier 5: Project instructions (Local Law)
  Tier 6: Tool output / live evidence
  Tier 7: Memory / declarative facts
  Tier 8: Precedent / handoff context
"""

from enum import IntEnum
from typing import Any


class Tier(IntEnum):
    """Hierarchy tiers from Article VII. Lower number = higher priority."""
    CONSTITUTION = 1
    CASE_COMMAND = 2
    STATUTES = 3
    REGULATIONS = 4
    LOCAL_LAW = 5
    EVIDENCE = 6
    MEMORY = 7
    PRECEDENT = 8


class ConstitutionalRule:
    """A single constitutional rule with its tier and enforcement logic."""

    def __init__(self, tier: Tier, article: str, rule: str):
        self.tier = tier
        self.article = article
        self.rule = rule

    def __repr__(self) -> str:
        return f"<Rule Tier={self.tier.value} Art.{self.article}> {self.rule}"


# ─── The Seven Articles ──────────────────────────────────────────────

CONSTITUTION = [
    ConstitutionalRule(Tier.CONSTITUTION, "I", "Identity: the agent is the instance, not the model card."),
    ConstitutionalRule(Tier.CONSTITUTION, "II", "Truth: tool results shall not be fabricated. Verification is mandatory."),
    ConstitutionalRule(Tier.CONSTITUTION, "III", "User Agency: the user is sovereign. The current request overrides all lower tiers."),
    ConstitutionalRule(Tier.CONSTITUTION, "IV", "Duty of Action: execute, do not narrate. Every turn shall make progress."),
    ConstitutionalRule(Tier.CONSTITUTION, "V", "Verification: every action shall leave evidence. Verify before declaring success."),
    ConstitutionalRule(Tier.CONSTITUTION, "VI", "Legacy: leave the workspace cleaner than found. State legible."),
    ConstitutionalRule(Tier.CONSTITUTION, "VII", "Hierarchy: when directives conflict, resolve by tier order."),
]


class HierarchyResolver:
    """Resolves conflicts between directives using the Constitutional hierarchy."""

    @staticmethod
    def resolve(*directives: tuple[Tier, str]) -> str:
        """
        Given a list of (tier, directive) tuples, return the highest-priority directive.

        Example:
            resolver.resolve(
                (Tier.MEMORY, "User prefers concise responses"),
                (Tier.CASE_COMMAND, "Give me full details"),
            )
            → "Give me full details"  (Tier 2 beats Tier 7)
        """
        if not directives:
            return ""
        # Sort by tier (lowest number = highest priority)
        sorted_directives = sorted(directives, key=lambda d: d[0].value)
        return sorted_directives[0][1]

    @staticmethod
    def is_overridable(source_tier: Tier, target_tier: Tier) -> bool:
        """Check if source_tier can override target_tier."""
        return source_tier.value < target_tier.value


# ─── Runtime enforcement ─────────────────────────────────────────────

def enforce_truth(claimed: Any, verified: Any) -> bool:
    """Article II enforcement: claimed facts must match verified evidence."""
    return claimed == verified


def enforce_verification(action: str, evidence_available: bool) -> str:
    """Article V enforcement: verify before declaring success."""
    if not evidence_available:
        return f"⚠️  Verification required: {action} — no evidence available."
    return f"✅ Verified: {action}"

