"""Control messages between a client and the broker.

Terminal bytes ride binary WebSocket messages; everything else is JSON text, and this
module is the only place that turns it into something the broker will act on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

OPS = frozenset({"create", "attach", "list", "kill", "resize"})

# The spec is an object rather than a positional tuple so it can grow. Growth means
# adding a key here; an unknown key is refused so a newer client fails loudly instead
# of silently losing the field it asked for.
SPEC_KEYS = frozenset({"role"})

# The broker builds argv from this, never a shell string — but a value that looks like
# a flag would still be read as one by the CLI receiving it.
ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

MAX_DIMENSION = 10_000


class ProtocolError(Exception):
    """A client sent something the broker will not act on."""


@dataclass(frozen=True)
class Control:
    op: str
    spec: dict = field(default_factory=dict)
    sid: str = ""
    cols: int = 0
    rows: int = 0
    after_seq: int | None = None
    # Carried by every op; the server compares it. This module never holds the secret.
    token: str = ""


def _validate_spec(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ProtocolError("spec must be an object")
    unknown = set(raw) - SPEC_KEYS
    if unknown:
        raise ProtocolError(f"unknown spec key(s): {', '.join(sorted(unknown))}")
    role = raw.get("role")
    if not isinstance(role, str) or not ROLE_RE.match(role):
        raise ProtocolError(f"role must match {ROLE_RE.pattern}")
    return {"role": role}


def _validate_size(msg: dict) -> tuple[int, int]:
    cols, rows = msg.get("cols"), msg.get("rows")
    for name, value in (("cols", cols), ("rows", rows)):
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= MAX_DIMENSION:
            raise ProtocolError(f"{name} must be an integer in 1..{MAX_DIMENSION}")
    return cols, rows


def parse_control(text: str | bytes) -> Control:
    """Parse and validate one control message. Raises :class:`ProtocolError`."""
    try:
        msg = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ProtocolError(f"malformed control message: {exc}") from exc
    if not isinstance(msg, dict):
        raise ProtocolError("control message must be an object")

    op = msg.get("op")
    if op not in OPS:
        raise ProtocolError(f"unknown op {op!r}; expected one of {', '.join(sorted(OPS))}")

    token = msg.get("token", "")
    if not isinstance(token, str):
        raise ProtocolError("token must be a string")

    if op == "list":
        return Control(op=op, token=token)
    if op == "kill":
        return Control(op=op, sid=_require_sid(msg), token=token)

    cols, rows = _validate_size(msg)
    if op == "resize":
        return Control(op=op, cols=cols, rows=rows, token=token)
    if op == "create":
        return Control(
            op=op, spec=_validate_spec(msg.get("spec")), cols=cols, rows=rows, token=token
        )

    after = msg.get("after_seq")
    if after is not None and (not isinstance(after, int) or isinstance(after, bool) or after < 0):
        raise ProtocolError("after_seq must be a non-negative integer")
    return Control(
        op=op, sid=_require_sid(msg), cols=cols, rows=rows, after_seq=after, token=token
    )


def _require_sid(msg: dict) -> str:
    sid = msg.get("sid")
    if not isinstance(sid, str) or not sid:
        raise ProtocolError("sid is required")
    return sid


def error(message: str) -> str:
    """Serialize a refusal for the client."""
    return json.dumps({"error": message})


def ok(**fields) -> str:
    """Serialize a success reply."""
    return json.dumps({"ok": True, **fields})


def event(name: str, **fields) -> str:
    """Serialize an unsolicited notification, e.g. the session exiting."""
    return json.dumps({"event": name, **fields})
