"""Lint Claude settings permission rules for known upstream pitfalls (HATS-1006).

Pure over parsed settings data — file IO and notice formatting live with the
caller (``WrapRunner._lint_claude_settings``). See docs/glossary.md
"Claude settings lint".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Claude Code >=2.1.210: file-permission checks match only Edit()/Read() rules.
DEPRECATED_RULE_TOOLS: tuple[tuple[str, str], ...] = (
    ("Write", "Edit"),
    ("NotebookEdit", "Edit"),
    ("Glob", "Read"),
)

_PERMISSION_ARRAYS = ("allow", "deny", "ask")


@dataclass(frozen=True)
class SettingsFinding:
    """One deprecated permission rule: where it is and what replaces it."""

    source: Path
    array: str
    rule: str
    replacement: str


def lint_permission_rules(settings: object, *, source: Path) -> list[SettingsFinding]:
    """Findings for every deprecated permission rule in one parsed settings doc.

    Tolerates any malformed shape (non-dict nodes, non-string rules) by
    skipping it — the caller's fail-open contract, applied at field level.
    """
    if not isinstance(settings, dict):
        return []
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        return []
    findings: list[SettingsFinding] = []
    for array in _PERMISSION_ARRAYS:
        rules = permissions.get(array)
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, str):
                continue
            for tool, replacement_tool in DEPRECATED_RULE_TOOLS:
                prefix = f"{tool}("
                if rule.startswith(prefix):
                    replacement = f"{replacement_tool}({rule[len(prefix) :]}"
                    findings.append(SettingsFinding(source, array, rule, replacement))
                    break
    return findings


def lint_settings_files(paths: Iterable[Path]) -> list[SettingsFinding]:
    """Findings across a settings-file chain; per-file fail-open.

    A missing, unreadable, or non-JSON file contributes nothing — a broken
    settings file is Claude Code's own loud failure, not this lint's.
    """
    findings: list[SettingsFinding] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        findings.extend(lint_permission_rules(data, source=path))
    return findings
