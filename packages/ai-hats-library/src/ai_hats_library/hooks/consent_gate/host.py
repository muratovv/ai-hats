#!/usr/bin/env python3
"""What the engine must be TOLD about its host — the only module here that knows ai-hats.

``check`` and ``issue`` take session, store and policy as arguments precisely so
they never read an environment or open an artefact of ours. This file is where
that knowledge is allowed to live, and the one that stays behind when the engine
ships on its own (HATS-1740).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .check import store_root_from

#: The envelope every ai-hats session publishes, and the app key whose rows say
#: which operation types a grant may cover.
IDENTITY_ENV = "AI_HATS_SESSION_IDENTITY"
CONSENT_GATE_APP = "consent_gate"


class NoSession(Exception):
    """There is no session to issue against — never a refusal, a different answer."""


def envelope(environ: dict[str, str] | None = None) -> dict:
    """The session envelope, or ``{}`` outside a session."""
    raw = (os.environ if environ is None else environ).get(IDENTITY_ENV, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}  # unreadable envelope reads as no session; the caller says so
    return data if isinstance(data, dict) else {}


def store_root(identity: dict) -> Path | None:
    """Where this session's grants live."""
    return store_root_from(identity.get("session_cache_dir"))


def policy(identity: dict) -> tuple[str, ...]:
    """Operation types the role declared under ``apps.consent_gate``.

    Read from the launch-frozen ``role_materialization.json`` — the same file
    the PreToolUse guard reads, so the verb cannot issue a grant for a type the
    gate would not honour.
    """
    session_dir = identity.get("session_dir") or ""
    if not session_dir:
        return ()
    try:
        report = json.loads(
            (Path(session_dir) / "role_materialization.json").read_text(encoding="utf-8")
        )
        rows = report.get("consent") or []
    except (OSError, ValueError):
        return ()  # nothing declared that we can see; the verb refuses and says so
    return tuple(
        str(row.get("selector", ""))
        for row in rows
        if isinstance(row, dict) and row.get("app") == CONSENT_GATE_APP and row.get("selector")
    )
