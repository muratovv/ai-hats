"""The status line as the HITL session's quota producer.

A PTY session has no SDK stream and its transcript carries no quota state; the
one thing that sees the rate-limit windows is the status-line command, which
Claude runs with a JSON payload on every render. The reading is a threshold —
``Notice(APPROACHING_LIMIT)`` once per window per reset — because a gauge on
every render would say one thing hundreds of times. The settings entry runs
this module and then the person's own status line, so their bar keeps rendering.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from ai_hats_observe.canonical import Notice, Timestamp, WorthRecording, now

from ...env import APPROACHING_LIMIT_PERCENT, read_budget
from ...session_identity import SessionIdentity, SessionIdentityError
from ..gate_log import record_event

#: What ``Notice.source`` says when the status line spoke.
SOURCE = "claude/statusline"

#: Beside the session's events: which windows were already said, by their reset.
MEMO_NAME = "quota_warnings.json"

#: The settings key claude reads the status line from — one slot, so a session's
#: `--settings` replaces the person's.
SETTINGS_KEY = "statusLine"

#: What that slot runs in a HITL session: this module, then the person's own
#: command with the same payload. A bar is not a gate: the recorder is skipped
#: without its interpreter, and the exit is 0 whatever either half said.
STATUSLINE_COMMAND = (
    "sh -c '"
    "p=$(cat); "
    'if [ -x "$AI_HATS_PYTHON" ]; then '
    'printf "%s" "$p" | "$AI_HATS_PYTHON" -m ai_hats.surfaces.claude.statusline; fi; '
    'if [ -n "$AI_HATS_STATUSLINE_INNER" ]; then '
    'printf "%s" "$p" | sh -c "$AI_HATS_STATUSLINE_INNER"; fi; '
    "exit 0'"
)

Warned = dict[str, int | None]


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


def status_line_entry(person: Mapping[str, Any] | None) -> dict[str, Any]:
    """The session's ``statusLine`` value: our command, with the person's own
    ``padding`` carried over so the bar sits where they put it."""
    entry: dict[str, Any] = {"type": "command", "command": STATUSLINE_COMMAND}
    if person is not None and "padding" in person:
        entry["padding"] = person["padding"]
    return entry


def quota_notices(
    payload: Mapping[str, Any], warned: Mapping[str, int | None], *, threshold: float, ts: Timestamp
) -> tuple[list[Notice], Warned]:
    """What one render says about quota: a notice per window at or past
    ``threshold`` percent that ``warned`` does not already hold for the same
    reset, and the memo to keep. Pure; a malformed window is skipped."""
    remembered: Warned = dict(warned)
    windows = payload.get("rate_limits")
    if not isinstance(windows, Mapping):
        return [], remembered
    said: list[Notice] = []
    for window, state in windows.items():
        if not isinstance(state, Mapping):
            continue
        used = state.get("used_percentage")
        if not _is_number(used) or used < threshold:
            continue
        resets_at = state.get("resets_at")
        reset = int(resets_at) if _is_number(resets_at) else None
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


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _detail(window: str, used: float, reset: int | None) -> str:
    head = f"{window} {used:.0f}% used"
    if reset is None:
        return head
    when = datetime.fromtimestamp(reset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{head}; resets at {when}"


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Read one render's payload, record what it says, remember what was said.

    Exit 0 on every path: a status line is not a gate, so its own trouble is
    said on stderr and never changes what the surface shows.
    """
    env = dict(os.environ if environ is None else environ)
    source = sys.stdin if stdin is None else stdin
    try:
        payload = json.loads(source.read())
    except (OSError, ValueError) as exc:
        return _said(f"unreadable payload: {exc}")
    if not isinstance(payload, dict):
        return _said(f"payload is {type(payload).__name__}, not an object")
    try:
        identity = SessionIdentity.from_env(env)
    except SessionIdentityError as exc:
        return _said(str(exc))
    if identity is None:
        return _said("no session in the environment; nothing recorded")

    memo = identity.session_dir / MEMO_NAME
    warned = _load(memo)
    threshold = read_budget(APPROACHING_LIMIT_PERCENT, env)
    events, remembered = quota_notices(payload, warned, threshold=threshold, ts=now())
    for event in events:
        record_event(event, env)
    if remembered != warned:
        _save(memo, remembered)
    return 0


def _said(what: str) -> int:
    print(f"ai-hats-statusline: {what}", file=sys.stderr)
    return 0


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


if __name__ == "__main__":
    sys.exit(main(sys.argv))
