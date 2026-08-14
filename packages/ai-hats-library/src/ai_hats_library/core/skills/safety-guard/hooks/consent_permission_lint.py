#!/usr/bin/env python3
"""HATS-1642 — report an allow-rule that switches the consent gate off.

Measured, not imagined: a live session walked `plan → execute` with no question
asked, because `"Bash(rack transition *)"` sat under `permissions.allow`. The
harness auto-approved the call, the guard still injected its ticket, and the log
said consent was accepted. A gate one config line disarms in silence is no gate.

Bound at ``ai-hats:startup`` with ``on_error: warn``: exit 1 to report (the
channel softens a BROKE run), never 2 — a REFUSE is not downgradable.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

#: Settings files a project or a user can put an allow-rule in, nearest first.
SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")

#: What auto-approval must never reach. Probes, not patterns: a rule is judged
#: by whether it would wave THESE through, so a narrow rule about a neighbouring
#: verb (`rack context *`, `rack transition * --log *`) stays untouched.
CONSENT_COMMANDS = (
    "rack transition HATS-1 execute",
    "rack transition HATS-1 --state execute",
)

#: The inline self-grant HATS-1639 refuses, fossilised into an allow-rule — the
#: shape found live, one line below the one that started this.
INLINE_GRANT = re.compile(r"AI_HATS_[A-Z0-9_]*(?:ACK|CONSENT_TICKET)\s*=")

AUTO_APPROVES = "auto-approves `plan → execute`, so the supervisor is never asked"
SELF_GRANT = "spells the inline consent self-grant the guard refuses (HATS-1639)"


class Finding(NamedTuple):
    line: int
    rule: str
    why: str


def covers(rule: str, command: str) -> bool:
    """True when a permission rule would auto-approve ``command``.

    The harness's own grammar, kept deliberately small: a bare tool name allows
    everything it names, and inside `Bash(…)` only `*` is a wildcard.
    """
    rule = rule.strip()
    if rule in ("*", "Bash"):
        return True
    if not (rule.startswith("Bash(") and rule.endswith(")")):
        return False
    inner = rule[len("Bash(") : -1].strip()
    if not inner:
        return False
    pattern = "".join(".*" if ch == "*" else re.escape(ch) for ch in inner)
    try:
        return re.fullmatch(pattern, command) is not None
    except re.error:
        return False


def _why(rule: str) -> str:
    if INLINE_GRANT.search(rule):
        return SELF_GRANT
    if any(covers(rule, command) for command in CONSENT_COMMANDS):
        return AUTO_APPROVES
    return ""


def _line_of(rule: str, lines: list[str]) -> int:
    for number, line in enumerate(lines, 1):
        if rule in line:
            return number
    return 0


def findings_in(text: str) -> list[Finding]:
    """Every allow-rule in ``text`` that disarms the consent gate, with its line.

    Unparseable settings are not a finding: this check reports what it can read,
    and a malformed file is a louder problem that belongs to whoever loads it.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    permissions = data.get("permissions")
    allow = permissions.get("allow") if isinstance(permissions, dict) else None
    if not isinstance(allow, list):
        return []
    lines = text.splitlines()
    out = []
    for rule in allow:
        if not isinstance(rule, str):
            continue
        why = _why(rule)
        if why:
            out.append(Finding(_line_of(rule, lines), rule, why))
    return out


def settings_paths(roots) -> list[Path]:
    """Every settings file under ``roots``, deduped by what it resolves to."""
    seen, paths = set(), []
    for root in roots:
        for name in SETTINGS_FILES:
            path = root / name
            try:
                key = path.resolve()
            except OSError:
                continue
            if key not in seen:
                seen.add(key)
                paths.append(path)
    return paths


def main(roots=None) -> int:
    """Report, never refuse. ``roots`` defaults to the two places a rule lives —
    resolved HERE rather than three frames down, so a caller can name its own."""
    reported = []
    for path in settings_paths(roots if roots is not None else (Path.cwd(), Path.home())):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # absent or unreadable: nothing this check can say
        reported.extend(f"{path}:{f.line}: {f.rule!r} — {f.why}" for f in findings_in(text))
    if not reported:
        return 0
    print("permissions.allow disarms the plan → execute consent gate:")
    for line in reported:
        print(f"  {line}")
    print(
        "Narrow or drop these rules — with one in place the transition is "
        "auto-approved and the supervisor is never asked."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
