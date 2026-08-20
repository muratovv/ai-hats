#!/usr/bin/env python3
"""HATS-1642 — report an allow-rule that switches the consent gate off.

Measured, not imagined: a live session walked `plan → execute` with no question
asked, because `"Bash(rack transition *)"` sat under `permissions.allow` — the
harness approved the call before any prompt could appear. A gate one config line
disarms in silence is no gate.

Carried by the PreToolUse hook, not a bound check: ADR-0019 D9 clause 4 will not
resolve a check whose skill sits in a linked worktree, and ai-hats is developed
from worktrees. The hook runs everywhere, and warns without blocking.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple

#: Settings files a project or a user can put an allow-rule in, nearest first.
SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")

#: What auto-approval must never reach, and which pause each one removes. Probes,
#: not patterns: a rule is judged by whether it would wave THESE through, so a
#: narrow rule about a neighbouring verb stays untouched.
GUARDED_COMMANDS = (
    ("rack transition HATS-1 execute", "the plan → execute consent question"),
    ("rack transition HATS-1 --state execute", "the plan → execute consent question"),
    ("ai-hats wt merge task/x", "the pause before a merge into master"),
    # HATS-1735: the issuing verb. Higher stakes than the rows above — under an
    # allow-rule the agent writes ITSELF a grant, silently, and a grant is wider
    # than the one-shot ticket those rows protect.
    ("consent all 30", "the question the agent's own `consent` attempt turns into"),
)

#: The inline self-grant HATS-1639 refuses, fossilised into an allow-rule — the
#: shape found live, one line below the one that started this.
INLINE_GRANT = re.compile(r"AI_HATS_[A-Z0-9_]*(?:ACK|CONSENT_TICKET)\s*=")

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
    if inner.endswith(":*"):
        # The harness's prefix idiom: `Bash(ai-hats:*)` is every ai-hats command,
        # which is how an allow-rule quietly covers `ai-hats wt merge`.
        prefix = inner[:-2]
        return command == prefix or command.startswith(prefix + " ")
    pattern = "".join(".*" if ch == "*" else re.escape(ch) for ch in inner)
    try:
        return re.fullmatch(pattern, command) is not None
    except re.error:
        return False


def _why(rule: str, restored=()) -> str:
    """Why ``rule`` is a finding — ``""`` when it silences nothing.

    ``restored`` holds the deny- and ask-rules. The harness resolves
    `deny -> ask -> allow` and weighs no specificity, so a guarded call one of
    them names is asked about anyway and this rule takes nothing away. Reported
    regardless: a fossilised inline self-grant, which the guard refuses on its
    own terms (HATS-1639).
    """
    if INLINE_GRANT.search(rule):
        return SELF_GRANT
    silenced = sorted(
        {
            why
            for command, why in GUARDED_COMMANDS
            if covers(rule, command) and not any(covers(other, command) for other in restored)
        }
    )
    if not silenced:
        return ""
    return "auto-approves the call, so " + " and ".join(silenced) + " never happens"


def _line_of(rule: str, lines: list[str]) -> int:
    for number, line in enumerate(lines, 1):
        if rule in line:
            return number
    return 0


def _rules(permissions: dict, key: str) -> list[str]:
    """The string rules under ``key`` — any other shape is skipped, not guessed at."""
    rules = permissions.get(key)
    if not isinstance(rules, list):
        return []
    return [rule for rule in rules if isinstance(rule, str)]


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
    if not isinstance(permissions, dict):
        return []
    allow = _rules(permissions, "allow")
    if not allow:
        return []
    restored = _rules(permissions, "deny") + _rules(permissions, "ask")
    lines = text.splitlines()
    out = []
    for rule in allow:
        why = _why(rule, restored)
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


def warning_for(roots=None) -> str:
    """The message to surface, or ``""`` when the allow-list leaves the gates alone.

    ``roots`` defaults to the two places a rule lives — resolved HERE rather than
    three frames down, so a caller can name its own.
    """
    reported = []
    for path in settings_paths(roots if roots is not None else (Path.cwd(), Path.home())):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # absent or unreadable: nothing this check can say
        reported.extend(f"  {path}:{f.line}: {f.rule!r} — {f.why}" for f in findings_in(text))
    if not reported:
        return ""
    return "\n".join(
        [
            "permissions.allow silences a guard that is meant to ask:",
            *reported,
            "Narrow or drop these rules. The routine rack calls are allowed by "
            "this guard itself, so no allow-rule is needed for them.",
        ]
    )


def already_warned(marker: Path, session: str) -> bool:
    """True when ``session`` has already been told; records it when it has not.

    One file, rewritten rather than accumulated: a warning worth repeating every
    session is not worth a directory of markers. Unwritable means "not warned",
    so the message repeats rather than vanishing.
    """
    try:
        seen = json.loads(marker.read_text(encoding="utf-8")).get("session_id")
    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
        seen = None
    if seen == session:
        return True
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"session_id": session}), encoding="utf-8")
    except OSError:
        pass
    return False
