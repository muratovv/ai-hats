#!/usr/bin/env python3
"""HATS-1647 — rule_backlog_discipline §1 as a PreToolUse gate. Denies, fails open.

A hand-written `task.yaml` desynchronises the FSM, its locks and its audit trail,
so `rack` is the only sanctioned writer. An Edit/Write/MultiEdit under
`<ai_hats_dir>/tracker/backlog/**` -> exit 0 + permissionDecision "deny" (binds
headless too, unlike "ask"); `tasks/<ID>/plan.md` is carved out (§1b) and
everything else is silent. Kill switch: AI_HATS_BACKLOG_GATE_OFF=1, exported.
Stdlib-only (system python3 via shebang). Zero egress.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# HATS-1407 — a bypass printed only to stderr leaves no trace an hour later.
# The hooks are stdlib-only, so the journal arrives as a flattened sibling.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bypass_journal import journal_bypass
except ImportError:  # helper absent -> say so; never skip quietly

    def journal_bypass(kind: str, reason: str, **_kw) -> bool:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False


_HOOK = "backlog_write_gate.py"
_KILL_SWITCH = "AI_HATS_BACKLOG_GATE_OFF"

_CONFIG_NAME = "ai-hats.yaml"
_DEFAULT_AI_HATS_DIR = ".agent/ai-hats"
#: The guarded subtree, relative to `ai_hats_dir`.
_BACKLOG_RELPATH = ("tracker", "backlog")
#: Layout of the one file the agent authors itself, relative to the backlog root.
_PLAN_RELPATH = ("tasks", "*", "plan.md")

_AI_HATS_DIR_RE = re.compile(r"^ai_hats_dir:[ \t]*[\"']?([^\"'\s]+)", re.MULTILINE)

DENY_REASON = (
    "GUARDRAIL (safety-guard): blocked — {rel} sits under the tracker backlog, and "
    "`rack` is its only sanctioned writer (rule_backlog_discipline §1): a hand-edited "
    "card desynchronises the state machine from its locks and audit trail. Move the "
    'card with `rack transition <ID> <state> --log "..."`, edit a field with '
    "`rack transition <ID> --set <field>=<value>`, and read it with `rack context <ID>`. "
    "A document is written outside the tracker and brought in: "
    "`rack transition <ID> --attach /tmp/summary.md:summary.md`. "
    "`tasks/<ID>/plan.md` is the one file here you may write directly. If the tracker "
    "itself is broken and only a raw edit can repair it, the supervisor exports "
    f"{_KILL_SWITCH}=1 for the session — there is no per-call override, because a "
    "guard the agent switches off is not a guard."
)


def _configured_ai_hats_dir(project: Path) -> Path:
    """`ai_hats_dir` of the project rooted at `project`, absolute.

    Read from `ai-hats.yaml` with the documented `.agent/ai-hats` default —
    deliberately NOT from $AI_HATS_DIR, which leaks between checkouts and would
    aim the gate at another tracker (same reasoning as done-gate.sh)."""
    try:
        text = (project / _CONFIG_NAME).read_text(encoding="utf-8")
    except OSError:
        text = ""
    match = _AI_HATS_DIR_RE.search(text)
    configured = Path(match.group(1)).expanduser() if match else Path(_DEFAULT_AI_HATS_DIR)
    return configured if configured.is_absolute() else project / configured


def _backlog_relpath(target: Path) -> tuple[str, ...] | None:
    """`target`'s path inside the backlog of the project that OWNS it, else None.

    Resolved by walking up from the target, so a session in a linked worktree
    editing the main checkout's tracker is judged by that tracker's config."""
    for project in target.parents:
        if not (project / _CONFIG_NAME).is_file():
            continue
        root = _configured_ai_hats_dir(project).joinpath(*_BACKLOG_RELPATH)
        if target.is_relative_to(root):
            return target.relative_to(root).parts
    return _default_layout_relpath(target)


def _default_layout_relpath(target: Path) -> tuple[str, ...] | None:
    """Fallback for a tracker with no reachable `ai-hats.yaml` — a linked worktree
    carries none, and a missing config must not quietly disarm the gate."""
    marker = tuple(Path(_DEFAULT_AI_HATS_DIR).parts) + _BACKLOG_RELPATH
    parts = target.parts
    for i in range(len(parts) - len(marker) + 1):
        if parts[i : i + len(marker)] == marker:
            return parts[i + len(marker) :]
    return None


def _is_plan_document(rel: tuple[str, ...]) -> bool:
    """The `rule_backlog_discipline` §1b carve-out: the plan is the agent's own
    deliverable, not FSM-owned state."""
    if len(rel) != len(_PLAN_RELPATH):
        return False
    return all(want in ("*", have) for want, have in zip(_PLAN_RELPATH, rel))


def _target_path(payload: dict) -> str:
    # Claude Code sends arguments in `tool_input`; Agy (Antigravity CLI) in
    # `toolCall.args`. Both are checked so the hook binds on either surface.
    tool_input = payload.get("tool_input")
    if not tool_input:
        tool_input = (payload.get("toolCall") or {}).get("args") or {}
    for key in ("file_path", "path", "target_file", "TargetFile", "AbsolutePath"):
        value = tool_input.get(key)
        if value:
            return str(value)
    return ""


def verdict_for(file_path: str) -> str:
    """The deny reason for writing `file_path`, or "" when it is none of our business."""
    if not file_path:
        return ""
    target = Path(os.path.abspath(os.path.expanduser(file_path)))
    rel = _backlog_relpath(target)
    if rel is None or _is_plan_document(rel):
        return ""
    return DENY_REASON.format(rel="/".join(rel) or target.name)


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        journal_bypass("hatch", _KILL_SWITCH, hook=_HOOK)
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:
        # Fail-open, but recorded (HATS-1373).
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook=_HOOK)
        return 0

    try:
        reason = verdict_for(_target_path(payload))
    except Exception as exc:
        journal_bypass("fail-open", f"path resolution failed: {exc!r}", hook=_HOOK)
        return 0

    if reason:
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
