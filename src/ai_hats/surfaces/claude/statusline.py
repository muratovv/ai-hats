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
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_hats_observe.canonical import Notice, Timestamp, WorthRecording, now

from ...env import APPROACHING_LIMIT_PERCENT, read_budget
from ...session_identity import SessionIdentity, SessionIdentityError

#: What ``Notice.source`` says when the status line spoke.
SOURCE = "claude/statusline"

#: Beside the session's events: which windows were already said, by their reset.
MEMO_NAME = "quota_warnings.json"

#: The settings key claude reads the status line from — one slot, so a session's
#: `--settings` replaces the person's.
SETTINGS_KEY = "statusLine"

#: The payload field only a status-line render carries; a hook's names its event.
RENDER_FIELD = "rate_limits"

Warned = dict[str, int | None]


def is_render(payload: Mapping[str, Any]) -> bool:
    """A status-line render, told from a hook's payload by what it carries."""
    return "hook_event_name" not in payload and RENDER_FIELD in payload


def quota_notice(payload: Mapping[str, Any], environ: Mapping[str, str]) -> Notice | None:
    """What one render says about quota that the session's log does not yet
    hold — one notice at most, the rest on the next render — remembered in the
    session dir so the same window is never said twice. Never raises: trouble
    is said on stderr, and a render is not a gate."""
    try:
        identity = SessionIdentity.from_env(dict(environ))
    except SessionIdentityError as exc:
        _said(str(exc))
        return None
    if identity is None:
        _said("no session in the environment; nothing recorded")
        return None
    memo = identity.session_dir / MEMO_NAME
    warned = _load(memo)
    threshold = read_budget(APPROACHING_LIMIT_PERCENT, dict(environ))
    notices, _ = quota_notices(payload, warned, threshold=threshold, ts=now())
    if not notices:
        return None
    first = notices[0]
    window = first.raw_code.split("=", 1)[0] if first.raw_code else ""
    _save(memo, {**warned, window: _reset_of(payload, window)})
    return first


def quota_notices(
    payload: Mapping[str, Any], warned: Mapping[str, int | None], *, threshold: float, ts: Timestamp
) -> tuple[list[Notice], Warned]:
    """What one render says about quota: a notice per window at or past
    ``threshold`` percent that ``warned`` does not already hold for the same
    reset, and the memo to keep. Pure; a malformed window is skipped."""
    remembered: Warned = dict(warned)
    windows = payload.get(RENDER_FIELD)
    if not isinstance(windows, Mapping):
        return [], remembered
    said: list[Notice] = []
    for window, state in windows.items():
        if not isinstance(state, Mapping):
            continue
        used = state.get("used_percentage")
        if not _is_number(used) or used < threshold:
            continue
        reset = _reset_of(payload, window)
        if window in remembered and remembered[window] == reset:
            continue
        remembered[window] = reset
        said.append(
            Notice(
                ts=ts,
                detail=_detail(window, used, reset),
                raw_code=f"{window}={used:.0f}%",
                source=SOURCE,
                reason=WorthRecording.APPROACHING_LIMIT,
            )
        )
    return said, remembered


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


def _reset_of(payload: Mapping[str, Any], window: str) -> int | None:
    state = payload.get(RENDER_FIELD, {}).get(window)
    resets_at = state.get("resets_at") if isinstance(state, Mapping) else None
    return int(resets_at) if _is_number(resets_at) else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _detail(window: str, used: float, reset: int | None) -> str:
    head = f"{window} {used:.0f}% used"
    if reset is None:
        return head
    when = datetime.fromtimestamp(reset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        staged = memo.with_name(f".{memo.name}.{os.getpid()}")
        staged.write_text(json.dumps(remembered, sort_keys=True), encoding="utf-8")
        os.replace(staged, memo)
    except OSError as exc:
        _said(f"memo not written: {exc}")
