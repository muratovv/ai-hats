"""Base of the rack typed-error hierarchy (HATS-1033).

Every CLI-surfaced domain error descends from :class:`RackError`; the dispatch
table in :mod:`.cli_common` owns one handler per concrete subclass and
``tests/test_error_surface.py`` pins that a new subclass without a reachable
handler fails CI (never a silent bare traceback).
"""

from __future__ import annotations

from pathlib import Path


class RackError(Exception):
    """Base for every rack domain error surfaced through the CLI error table."""


class RackConfigError(RackError):
    """A loaded config file (fsm.yaml / links.yaml / catalog) is malformed.

    Structural invariant, not a user refusal: the CLI error table routes the
    whole subtree to a single ``internal`` marker.
    """


class ForeignProjectPinError(RackError):
    """AI_HATS_PROJECT_DIR pin points to a different project directory."""

    def __init__(self, pin: Path, project_dir: Path, ai_hats_dir: Path | None = None) -> None:
        self.pin = pin
        self.project_dir = project_dir
        self.ai_hats_dir = ai_hats_dir
        sbx_msg = f" while AI_HATS_DIR points to '{ai_hats_dir}'" if ai_hats_dir else ""
        super().__init__(
            f"AI_HATS_PROJECT_DIR pin points to foreign project '{pin}' (current project: '{project_dir}')"
            f"{sbx_msg}: resolver refuses to guess target backlog. "
            "Pass --tasks-dir explicitly or update/unset AI_HATS_PROJECT_DIR and AI_HATS_DIR."
        )
