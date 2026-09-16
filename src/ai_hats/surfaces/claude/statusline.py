"""The status line as the HITL session's quota producer.

A PTY session has no SDK stream and its transcript carries no quota state; the
one thing that sees the rate-limit windows is the status-line command, which
Claude runs with a JSON payload on every render. The reading is a threshold —
``Notice(APPROACHING_LIMIT)`` once per window per reset — because a gauge on
every render would say one thing hundreds of times. The payload reaches the
session's dispatcher like any hook's; ``ClaudeChannel.observe`` calls here.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_hats_observe.canonical import Notice, Timestamp, WorthRecording, now

from ...env import APPROACHING_LIMIT_PERCENT, read_budget
from ...paths import claude_settings_json, claude_settings_local_json
from ...session_identity import SessionIdentity, SessionIdentityError
from ..gate_log import record_event

#: What ``Notice.source`` says when the status line spoke.
SOURCE = "claude/statusline"

#: Beside the session's events: which windows were already said, by their reset.
MEMO_NAME = "quota_warnings.json"

#: The settings key claude reads the status line from — one slot, so a session's
#: `--settings` replaces the person's.
SETTINGS_KEY = "statusLine"

#: The payload field only a status-line render carries.
RENDER_FIELD = "rate_limits"

#: What a render's window said: its name, its reset, the notice.
Said = tuple[str, "int | None", Notice]

Warned = dict[str, int | None]

# The resident dispatcher answers renders from threads of one process.
_MEMO_LOCK = threading.Lock()


def is_render(payload: Mapping[str, Any]) -> bool:
    """A status-line render, told by what it carries — never by what it lacks."""
    return RENDER_FIELD in payload


def record_quota(payload: Mapping[str, Any], environ: Mapping[str, str]) -> list[Notice]:
    """Record what one render says about quota beside the session's events and
    remember it, so the same window is never said twice: the memo follows the
    record, never precedes it. What was recorded; never raises — a render is
    not a gate, so trouble is said on stderr."""
    try:
        identity = SessionIdentity.from_env(dict(environ))
    except SessionIdentityError as exc:
        _said(str(exc))
        return []
    if identity is None:
        _said("no session in the environment; nothing recorded")
        return []
    memo = identity.session_dir / MEMO_NAME
    threshold = read_budget(APPROACHING_LIMIT_PERCENT, dict(environ))
    with _MEMO_LOCK:
        warned = _load(memo)
        recorded: list[Notice] = []
        for window, reset, notice in quota_notices(payload, warned, threshold=threshold, ts=now()):
            if record_event(notice, environ) is None:
                continue
            warned[window] = reset
            recorded.append(notice)
        if recorded:
            _save(memo, warned)
    return recorded


def quota_notices(
    payload: Mapping[str, Any], warned: Mapping[str, int | None], *, threshold: float, ts: Timestamp
) -> list[Said]:
    """What one render says about quota: a notice per window at or past
    ``threshold`` percent that ``warned`` does not already hold for the same
    reset. Pure; a malformed window is skipped."""
    windows = payload.get(RENDER_FIELD)
    if not isinstance(windows, Mapping):
        return []
    said: list[Said] = []
    for window, state in windows.items():
        if not isinstance(state, Mapping) or not isinstance(window, str):
            continue
        used = state.get("used_percentage")
        if not _is_finite(used) or used < threshold:
            continue
        resets_at = state.get("resets_at")
        reset = int(resets_at) if _is_finite(resets_at) else None
        if window in warned and warned[window] == reset:
            continue
        notice = Notice(
            ts=ts,
            detail=_detail(window, used, reset),
            raw_code=f"{window}={used:.0f}%",
            source=SOURCE,
            reason=WorthRecording.APPROACHING_LIMIT,
        )
        said.append((window, reset, notice))
    return said


def person_settings_files(environ: Mapping[str, str], base: Path) -> list[Path]:
    """Claude's settings chain — user, project, local — as claude layers it.
    The user's home by the rule ``tool_home`` applies, read from ``environ``."""
    config_dir = environ.get("CLAUDE_CONFIG_DIR") or ""
    home = environ.get("HOME") or ""
    files: list[Path] = []
    if config_dir:
        files.append(Path(config_dir) / "settings.json")
    elif home:
        files.append(Path(home) / ".claude" / "settings.json")
    files += [claude_settings_json(base), claude_settings_local_json(base)]
    return files


def person_status_line(settings_files: Sequence[Path]) -> dict[str, Any] | None:
    """The status line the person configured, read the way claude layers its
    settings: each file's ``statusLine`` replaces the one before it. ``None``
    when no file names a command; a file that will not parse is skipped aloud."""
    found: dict[str, Any] | None = None
    for path in settings_files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            _said(f"{path}: skipped, {exc}")
            continue
        entry = document.get(SETTINGS_KEY) if isinstance(document, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get("command"), str) and entry["command"]:
            found = dict(entry)
    return found


def _is_finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _detail(window: str, used: float, reset: int | None) -> str:
    head = f"{window} {used:.0f}% used"
    if reset is None:
        return head
    try:
        when = datetime.fromtimestamp(reset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return f"{head}; resets at {reset}"  # not a second of any calendar — kept as sent
    return f"{head}; resets at {when}"


def _said(what: str) -> None:
    print(f"ai-hats-statusline: {what}", file=sys.stderr)


def _load(memo: Path) -> Warned:
    try:
        loaded = json.loads(memo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        _said(f"memo unreadable, starting over: {exc}")
        return {}
    return {str(k): v for k, v in loaded.items()} if isinstance(loaded, dict) else {}


def _save(memo: Path, remembered: Warned) -> None:
    try:
        memo.parent.mkdir(parents=True, exist_ok=True)
        staged = memo.with_name(f".{memo.name}.{os.getpid()}.{threading.get_ident()}")
        staged.write_text(json.dumps(remembered, sort_keys=True), encoding="utf-8")
        os.replace(staged, memo)
    except OSError as exc:
        _said(f"memo not written: {exc}")
