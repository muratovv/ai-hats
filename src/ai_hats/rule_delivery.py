"""HATS-700 — rule-delivery contract checker.

Invariant: every ``see rule `X` `` pointer in a shipped trait/role injection must
point at a rule whose guidance actually reaches the agent — either

  * ``X`` is always-on (full body delivered into the prompt; ``ALWAYS_ON_RULES``), or
  * ``X``'s essence is summarized inline in the injection carrying the pointer,
    and ``X`` is registered in :data:`SUMMARIZED_IN_INJECTION`.

A pointer to an undelivered, unregistered rule is the HATS-700 bug class: the
agent is told "see rule X" for a rule it can never read. This checker is the
single source of that invariant — the G2 unit test runs it over the whole
shipped library; the ``rule-delivery-gate`` pre-commit hook runs it (via
``python -m ai_hats.rule_delivery``) over staged ``library/**/config.yaml`` edits.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .providers import ALWAYS_ON_RULES

# Non-always-on rules whose essence is intentionally summarized inline in the
# injection that points at them. Their full body is NOT delivered (provenance
# only) — the delivered summary is the canonical channel. Adding a ``see rule X``
# pointer to a NEW non-always-on rule REQUIRES a conscious entry here; that is
# the guarantee — a silent gap (HATS-700) becomes a red gate (G2 / pre-commit).
SUMMARIZED_IN_INJECTION: frozenset[str] = frozenset(
    {
        "rule_backlog_discipline",
        # dev_rule_comment_discipline moved to ALWAYS_ON_RULES (HATS-842) — its
        # few-shot body is now delivered in full, so the `see rule` pointer in
        # trait-se-mindset resolves through the always-on channel, not here.
        "dev_rule_e2e_gate",
        "rule_harness_reminder_hygiene",
        "rule_core_vs_usage_split",
    }
)

# Matches the shipped convention: ``see rule `rule_name` `` (backtick-quoted).
# Scoped to this phrasing so prose that merely names a rule is not flagged.
_SEE_RULE = re.compile(r"see rules?\s+`([a-z0-9_]+)`", re.IGNORECASE)


@dataclass(frozen=True)
class DanglingPointer:
    """A ``see rule X`` pointer whose rule reaches the agent through no channel."""

    rule: str
    source: str  # library-relative path of the config carrying the pointer


def find_dangling_rule_pointers(library_root: Path) -> list[DanglingPointer]:
    """Return every ``see rule X`` pointer in ``library_root`` for a rule that is
    neither always-on nor summarized-in-injection (i.e. undelivered)."""
    deliverable = set(ALWAYS_ON_RULES) | set(SUMMARIZED_IN_INJECTION)
    violations: list[DanglingPointer] = []
    for cfg in sorted(library_root.rglob("config.yaml")):
        rel = str(cfg.relative_to(library_root))
        for match in _SEE_RULE.finditer(cfg.read_text()):
            rule = match.group(1)
            if rule not in deliverable:
                violations.append(DanglingPointer(rule=rule, source=rel))
    return violations


def _main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path("library")
    violations = find_dangling_rule_pointers(root)
    if not violations:
        return 0
    print(
        "HATS-700 rule-delivery contract violated — `see rule X` pointing at a "
        "rule the agent cannot read:",
        file=sys.stderr,
    )
    for v in violations:
        print(
            f"  {v.source}: see rule `{v.rule}` — not in ALWAYS_ON_RULES nor "
            "SUMMARIZED_IN_INJECTION",
            file=sys.stderr,
        )
    print(
        "\nFix one of: make the rule always-on (providers.ALWAYS_ON_RULES); fold "
        "its essence into the injection and register it in SUMMARIZED_IN_INJECTION "
        "(ai_hats/rule_delivery.py); or drop the pointer.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
