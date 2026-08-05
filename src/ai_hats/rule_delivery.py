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
``python -m ai_hats.rule_delivery``) over a commit's staged library config.yaml files.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from typing import Sequence

import yaml

from .constants import ALWAYS_ON_RULES
from .models import RuleMetadata

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

# Matches conventions: ``see rule `rule_name` ``, ``see `rule_name` ``, ``see rules `rule_name` `` (case insensitive, hyphens + underscores).
_SEE_RULE = re.compile(r"see\s+(?:rules?\s+)?`([a-z0-9_-]+)`", re.IGNORECASE)


@dataclass(frozen=True)
class DanglingPointer:
    """A ``see rule X`` pointer whose rule reaches the agent through no channel."""

    rule: str
    source: str  # library-relative path of the config carrying the pointer


def _get_search_roots(roots: list[Path]) -> list[Path]:
    all_roots = list(roots)
    try:
        from .library_paths import build_library_paths

        for p in build_library_paths():
            if p not in all_roots and p.exists():
                all_roots.append(p)
    except Exception:  # noqa: S110, BLE001 # silent-ok: fallback when build_library_paths unavailable
        pass
    return all_roots


def _is_trait_or_skill(name: str, roots: list[Path]) -> bool:
    all_roots = _get_search_roots(roots)
    for root in all_roots:
        if (root / "traits" / name).is_dir() or (root / "skills" / name).is_dir():
            return True
        if list(root.rglob(f"traits/{name}")) or list(root.rglob(f"skills/{name}")):
            return True
    return False


def _is_rule_deliverable(rule_name: str, roots: list[Path]) -> bool:
    if rule_name in ALWAYS_ON_RULES or rule_name in SUMMARIZED_IN_INJECTION:
        return True
    all_roots = _get_search_roots(roots)
    for root in all_roots:
        direct_meta = root / "rules" / rule_name / "metadata.yaml"
        meta_paths = (
            [direct_meta]
            if direct_meta.is_file()
            else list(root.rglob(f"rules/{rule_name}/metadata.yaml"))
        )
        for meta_path in meta_paths:
            if meta_path.is_file():
                try:
                    meta = RuleMetadata.from_yaml(meta_path)
                    if meta.delivery == "always_on":
                        return True
                except Exception:  # noqa: S110, BLE001 # silent-ok: malformed metadata treated as non-always-on
                    pass
    return False


def find_dangling_rule_pointers(
    library_root: Path | Sequence[Path] | None = None,
) -> list[DanglingPointer]:
    """Return every ``see rule X`` pointer and undelivered ``composition.rules`` item
    in ``library_root`` (or default library paths) for a rule that reaches the agent through no channel.
    """
    if library_root is None:
        try:
            from .library_paths import build_library_paths

            roots = build_library_paths()
        except Exception:  # noqa: S110, BLE001 # silent-ok: fallback when build_library_paths unavailable
            roots = [_installed_library_root()]
    elif isinstance(library_root, (Path, str)):
        roots = [Path(library_root)]
    else:
        roots = [Path(p) for p in library_root]

    violations: list[DanglingPointer] = []
    seen: set[tuple[str, str]] = set()

    for root in roots:
        if not root.exists():
            continue
        for cfg in sorted(root.rglob("config.yaml")):
            try:
                rel = str(cfg.relative_to(root))
            except ValueError:
                rel = str(cfg)

            text = cfg.read_text()

            # 1. Prose pointers matching see rule `X` or see `X`
            for match in _SEE_RULE.finditer(text):
                rule = match.group(1)
                if _is_trait_or_skill(rule, roots):
                    continue
                if not _is_rule_deliverable(rule, roots):
                    key = (rule, rel)
                    if key not in seen:
                        seen.add(key)
                        violations.append(DanglingPointer(rule=rule, source=rel))

            # 2. Structural composition.rules: [X, ...]
            try:
                data = yaml.safe_load(text) or {}
                if isinstance(data, dict):
                    comp = data.get("composition")
                    if isinstance(comp, dict):
                        rules = comp.get("rules")
                        if isinstance(rules, list):
                            for rule in rules:
                                if isinstance(rule, str) and not _is_rule_deliverable(rule, roots):
                                    key = (rule, rel)
                                    if key not in seen:
                                        seen.add(key)
                                        violations.append(DanglingPointer(rule=rule, source=rel))
            except Exception:  # noqa: S110, BLE001 # silent-ok: non-yaml or malformed config ignored in structural parse
                pass

    return violations


def _installed_library_root() -> Path:
    """The shipped library tree, resolved from the package (HATS-1437)."""
    import ai_hats_library

    return Path(ai_hats_library.__file__).parent


def _main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    roots = [Path(a) for a in args] if args else None
    violations = find_dangling_rule_pointers(roots)
    if not violations:
        return 0
    print(
        "Rule-delivery contract violated — `see rule X` pointing at a rule the agent cannot read:",
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
