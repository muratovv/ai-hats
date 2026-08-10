"""HATS-700 / HATS-1515 — rule-delivery contract checker.

Invariant: every ``see rule `X` `` pointer in a shipped trait/role injection must
point at a rule that exists in the library.

Every composed rule's body is delivered into the prompt under ## RULES.
A pointer to a non-existent rule is a violation: the agent is told "see rule X" for
a rule that does not exist.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml

# Matches conventions: ``see rule `rule_name` ``, ``see `rule_name` ``, ``see rules `rule_name` `` (case insensitive, hyphens + underscores).
_SEE_RULE = re.compile(r"see\s+(?:rules?\s+)?`([a-z0-9_-]+)`", re.IGNORECASE)


@dataclass(frozen=True)
class DanglingPointer:
    """A ``see rule X`` pointer to a rule that does not exist."""

    rule: str
    source: str  # library-relative path of the config carrying the pointer


def _default_library_paths(project_dir: Path | None = None) -> list[Path]:
    p = project_dir or Path.cwd()
    try:
        from .library_paths import build_library_paths

        return build_library_paths(p)
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning("Failed to build library paths for %s: %s", p, exc)
        return [_installed_library_root()]


def _get_search_roots(roots: list[Path], project_dir: Path | None = None) -> list[Path]:
    all_roots = list(roots)
    for p in _default_library_paths(project_dir):
        if p not in all_roots and p.exists():
            all_roots.append(p)
    return all_roots


def _is_trait_or_skill(name: str, roots: list[Path], project_dir: Path | None = None) -> bool:
    all_roots = _get_search_roots(roots, project_dir)
    for root in all_roots:
        if (root / "traits" / name).is_dir() or (root / "skills" / name).is_dir():
            return True
        if list(root.rglob(f"traits/{name}")) or list(root.rglob(f"skills/{name}")):
            return True
    return False


def _rule_exists(rule_name: str, roots: list[Path], project_dir: Path | None = None) -> bool:
    all_roots = _get_search_roots(roots, project_dir)
    for root in all_roots:
        if (root / "rules" / rule_name).is_dir():
            return True
        if list(root.rglob(f"rules/{rule_name}")):
            return True
    return False


def find_dangling_rule_pointers(
    library_root: Path | Sequence[Path] | None = None,
    project_dir: Path | None = None,
) -> list[DanglingPointer]:
    """Return every ``see rule X`` pointer and ``composition.rules`` item
    in ``library_root`` (or default library paths) for a rule that does not exist in the library.
    """
    if library_root is None:
        roots = _default_library_paths(project_dir)
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
                if _is_trait_or_skill(rule, roots, project_dir):
                    continue
                if not _rule_exists(rule, roots, project_dir):
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
                                if isinstance(rule, str) and not _rule_exists(
                                    rule, roots, project_dir
                                ):
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
        "Rule-delivery contract violated — `see rule X` pointing at a rule that does not exist in the library:",
        file=sys.stderr,
    )
    for v in violations:
        print(
            f"  {v.source}: see rule `{v.rule}` — rule directory not found in library",
            file=sys.stderr,
        )
    print(
        "\nFix one of: create the missing rule in library/core/rules or library/usage/rules, or fix/drop the pointer.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
