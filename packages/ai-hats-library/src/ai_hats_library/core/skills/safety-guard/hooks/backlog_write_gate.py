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


def _normalise(file_path: str) -> Path:
    """An absolute path with `~`, `..` and symlinks resolved.

    `realpath` is half of not being fooled by a route: `ln -s` into the backlog
    costs one command, and a string match never sees it."""
    return Path(os.path.realpath(os.path.expanduser(file_path)))


def _same_dir(a: Path, b: Path) -> bool:
    """Whether two paths name the same directory ON DISK. False when either is
    missing — the caller then keeps the lexical answer."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _relpath_under(target: Path, root: Path) -> tuple[str, ...] | None:
    """`target`'s parts below `root`, or None when it lies outside.

    Lexically first: no syscall, and it answers the ordinary case. The identity
    walk behind it is the other half of not being fooled — on a case-folding
    filesystem `.Agent/…` and `.agent/…` are ONE directory (same inode), which
    no string comparison can know."""
    if target.is_relative_to(root):
        return target.relative_to(root).parts
    if not root.is_dir():
        return None
    node, tail = target, []
    while True:
        if _same_dir(node, root):
            return tuple(tail)
        parent = node.parent
        if parent == node:
            return None
        tail.insert(0, node.name)
        node = parent


def _configured_ai_hats_dir(project: Path) -> Path:
    """`ai_hats_dir` of the project rooted at `project`, absolute.

    Read from `ai-hats.yaml` with the documented `.agent/ai-hats` default —
    deliberately NOT from $AI_HATS_DIR, which leaks between checkouts and would
    aim the gate at another tracker (same reasoning as done-gate.sh)."""
    try:
        text = (project / _CONFIG_NAME).read_text(encoding="utf-8")
    except (OSError, ValueError):  # unreadable, or not text at all
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
        try:
            root = _normalise(str(_configured_ai_hats_dir(project).joinpath(*_BACKLOG_RELPATH)))
        except Exception:  # noqa: S112 # silent-ok: a config this gate cannot read is
            # not a licence to stop guarding — the default layout answers below, and
            # "no config -> protected, broken config -> open" is the wrong way round.
            continue
        rel = _relpath_under(target, root)
        if rel is not None:
            return rel
    return _default_layout_relpath(target)


def _default_layout_relpath(target: Path) -> tuple[str, ...] | None:
    """Fallback for a tracker with no reachable `ai-hats.yaml` — a linked worktree
    carries none, and a missing config must not quietly disarm the gate.

    Case-folded, for the same reason the identity walk exists. It over-reaches
    (any path literally shaped like the default layout answers here, project or
    not); deliberately, and recorded as such in the card's plan."""
    marker = tuple(p.lower() for p in Path(_DEFAULT_AI_HATS_DIR).parts) + _BACKLOG_RELPATH
    parts = target.parts
    for i in range(len(parts) - len(marker) + 1):
        if tuple(p.lower() for p in parts[i : i + len(marker)]) == marker:
            return parts[i + len(marker) :]
    return None


def _is_plan_document(rel: tuple[str, ...]) -> bool:
    """The `rule_backlog_discipline` §1b carve-out: the plan is the agent's own
    deliverable, not FSM-owned state."""
    if len(rel) != len(_PLAN_RELPATH):
        return False
    return all(want in ("*", have) for want, have in zip(_PLAN_RELPATH, rel))


def _target_path(payload: dict) -> str:
    # One dialect: the surface's bridge translates before spawning this
    # (`ai_hats.surfaces.agy.claude_hook_adapter`, HATS-1776).
    tool_input = payload.get("tool_input") or {}
    for key in ("file_path", "path"):
        value = tool_input.get(key)
        if value:
            return str(value)
    return ""


#: One line per process, and only once something was actually waved through —
#: `_acked()`'s discipline. Asking on every Edit would spend three git
#: subprocesses per call and bury the one interesting line.
_switch_journaled = False


def _switch_off() -> bool:
    """The supervisor's exported hatch. Consulted HERE, where the shared verdict
    is formed, so both halves of the gate honour the same answer: emergency
    tracker repair is raw shell, and a switch only the file tools obey points
    the agent at a wall."""
    global _switch_journaled
    if os.environ.get(_KILL_SWITCH) != "1":
        return False
    if not _switch_journaled:
        _switch_journaled = True
        journal_bypass("hatch", _KILL_SWITCH)
    return True


def verdict_for(file_path: str) -> str:
    """The deny reason for writing `file_path`, or "" when it is none of our business.

    Never raises: a hostile `ai-hats.yaml` reached through this path would
    otherwise take down the whole Bash gate with it, and `rm -rf /` alongside."""
    try:
        if not file_path:
            return ""
        target = _normalise(file_path)
        rel = _backlog_relpath(target)
        if rel is None or _is_plan_document(rel):
            return ""
        if _switch_off():
            return ""
        return DENY_REASON.format(rel="/".join(rel) or target.name)
    except Exception as exc:
        journal_bypass("fail-open", f"cannot judge {file_path!r}: {exc!r}")
        return ""


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:
        # Fail-open, but recorded (HATS-1373).
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook=_HOOK)
        return 0

    reason = verdict_for(_target_path(payload))
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
