#!/usr/bin/env python3
"""Refuse a Python edit that adds an inert broad except.

``violations()`` is also what ``scripts/check_silent_fallback.py`` walks.
PreToolUse refuses only when the edit grows that set.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bypass_journal import journal_bypass, journal_catch
except ImportError:  # helper absent -> say so; never skip quietly

    def journal_bypass(kind: str, reason: str, **_kw) -> bool:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False

    def journal_catch(rule: str, verdict: str, **_kw) -> bool:
        print(
            f"[catch-journal] NOT RECORDED ({rule}: {verdict}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False


_HOOK = "silent_fallback_gate.py"
_KILL_SWITCH = "AI_HATS_SILENT_FALLBACK_OFF"
MARKER = "# silent-ok:"
BROAD = frozenset({"Exception", "BaseException"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    source: str

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.line}: `{self.source}` catches broadly and its body "
            f"neither raises nor calls anything — the failure cannot reach a log, a "
            f"test, or a caller. Report it, re-raise it, or mark the site "
            f"`{MARKER} <reason>`."
        )


def is_broad(handler: ast.ExceptHandler) -> bool:
    """A bare ``except``, or one naming Exception/BaseException."""
    if handler.type is None:
        return True
    caught = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for node in caught:
        name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
        if name in BROAD:
            return True
    return False


def is_inert(handler: ast.ExceptHandler) -> bool:
    """Nothing in the body can carry the failure outward."""
    for statement in handler.body:
        for node in ast.walk(statement):
            if isinstance(node, (ast.Call, ast.Raise)):
                return False
            if handler.name and isinstance(node, ast.Name) and node.id == handler.name:
                return False
    return True


def is_marked(handler: ast.ExceptHandler, lines: list[str]) -> bool:
    """``# silent-ok:`` anywhere in the handler's line span."""
    return any(MARKER in line for line in lines[handler.lineno - 1 : handler.end_lineno])


def violations(source: str, path: str) -> list[Violation]:
    """Every inert broad handler in one module."""
    lines = source.splitlines()
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if is_broad(node) and is_inert(node) and not is_marked(node, lines):
            found.append(Violation(path, node.lineno, lines[node.lineno - 1].strip()))
    return sorted(found, key=lambda v: (v.path, v.line))


def _in_scope(file_path: str) -> bool:
    path = Path(file_path)
    return path.suffix == ".py" and "tests" not in path.parts


def _apply(text: str, edits: list[dict]) -> str | None:
    for edit in edits:
        old = edit.get("old_string")
        new = edit.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or old == "":
            return None
        found = text.count(old)
        if edit.get("replace_all"):
            if found == 0:
                return None
            text = text.replace(old, new)
            continue
        if found != 1:
            return None
        text = text.replace(old, new, 1)
    return text


def _prospective(payload: dict, file_path: str) -> str | None:
    tool_input = payload.get("tool_input") or {}
    if "edits" in tool_input or "old_string" in tool_input:
        if not os.path.isfile(file_path):
            return None
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            edits = [tool_input]
        return _apply(Path(file_path).read_text(encoding="utf-8"), edits)
    content = tool_input.get("content", tool_input.get("contents"))
    if isinstance(content, str):
        return content
    return None


def _before(file_path: str) -> str:
    if os.path.isfile(file_path):
        return Path(file_path).read_text(encoding="utf-8")
    return ""


def _grown(file_path: str, after: str) -> list[Violation]:
    before_found = violations(_before(file_path), file_path)
    after_found = violations(after, file_path)
    grown = Counter(v.source for v in after_found) - Counter(v.source for v in before_found)
    return [v for v in after_found if v.source in grown]


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        journal_bypass("hatch", _KILL_SWITCH, hook=_HOOK)
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook=_HOOK)
        return 0

    file_path = str((payload.get("tool_input") or {}).get("file_path") or "")
    if not _in_scope(file_path):
        return 0

    after = _prospective(payload, file_path)
    if after is None:
        journal_bypass("fail-open", f"cannot reconstruct {file_path!r}", hook=_HOOK)
        return 0
    try:
        fresh = _grown(file_path, after)
    except SyntaxError as exc:
        journal_bypass("fail-open", f"{file_path!r} does not parse: {exc!r}", hook=_HOOK)
        return 0
    if not fresh:
        return 0

    reason = (
        "GUARDRAIL (silent-fallback-gate): blocked — this edit adds a broad except "
        "whose body cannot report the failure.\n"
        + "\n".join(str(item) for item in fresh)
    )
    journal_catch("silent_fallback_gate", "deny", hook=_HOOK, cmd=file_path)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
