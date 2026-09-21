"""Which role the create-time worktree carry composes for: the session's.

The card's ``role`` says who should do the task; the session says who is doing
it. A worktree provisioned for the card's role got no ``wt_in`` hooks when the
two disagreed, and nothing said so.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout
from ai_hats_rack.dispatch import DispatchContext
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.models import TaskCard

from ai_hats.rack_wiring import WorktreeExtension
from ai_hats.session_identity import (
    ENV_SESSION_ID,
    ENV_SESSION_IDENTITY,
    SessionIdentity,
    SessionIdentityError,
)
from ai_hats.wt_effects import carry_role


def _session_env(project: Path, role: str) -> dict[str, str]:
    return SessionIdentity(
        id="s-1",
        role=role,
        provider="claude",
        project_dir=project,
        session_dir=project / ".agent" / "ai-hats" / "sessions" / "runs" / "s-1",
    ).to_env()


# ----- carry_role: the one producer of the carry's role -----------------------


def test_carry_role_is_the_governing_sessions_role(tmp_path: Path):
    assert carry_role(tmp_path, _session_env(tmp_path, "role-curator")) == "role-curator"


def test_carry_role_ignores_a_foreign_session(tmp_path: Path):
    """A session of another project is no session here: the config answers."""
    other = tmp_path / "other"
    assert carry_role(tmp_path, _session_env(other, "role-curator")) == ""


def test_carry_role_is_empty_outside_a_session(tmp_path: Path):
    assert carry_role(tmp_path, {}) == ""


def test_carry_role_degrades_on_a_too_old_envelope_and_says_so(tmp_path: Path, capsys):
    """An id with no envelope is a session an older ai-hats launched — nothing
    is torn, so the config answers; but the degradation reaches the human."""
    assert carry_role(tmp_path, {ENV_SESSION_ID: "s-old"}) == ""
    err = capsys.readouterr().err
    assert "s-old" in err and "configured role" in err, err


def test_carry_role_refuses_a_torn_envelope(tmp_path: Path):
    with pytest.raises(SessionIdentityError):
        carry_role(tmp_path, {ENV_SESSION_IDENTITY: "{not json"})


# ----- the execute edge: session, not card -----------------------------------


class _RecordingEffects:
    """Records the role ``setup`` was handed; creates nothing."""

    def __init__(self) -> None:
        self.roles: list[str] = []

    def setup(self, task_id, role="", caller_cwd=None, *, outer_deadline=None):
        self.roles.append(role)
        return Path("/nonexistent/wt")

    def assert_canonical_base(self):
        return None


def test_execute_edge_provisions_for_the_session_not_the_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The card says behaviorist; the session doing the work is role-curator.

    Before the fix ``setup`` received ``'behaviorist'`` — a usage role that
    composes no ``worktree-venv`` — and the worktree came up without a venv.
    """
    for key, value in _session_env(tmp_path, "role-curator").items():
        monkeypatch.setenv(key, value)
    effects = _RecordingEffects()
    ext = WorktreeExtension(ProjectLayout.at(tmp_path), effects=effects)

    delta = ext.on_event(
        DispatchContext(
            event=EdgeEvent(from_state="plan", to_state="execute"),
            task=TaskCard(id="T-1", role="behaviorist"),
            caller_cwd=tmp_path,
            is_epic=False,
            actor="test",
        )
    )

    assert effects.roles == ["role-curator"]
    assert delta is not None
    # The path line stays a bare `Worktree: <path>` — readers split on the colon.
    assert delta.work_log[0] == "Worktree: /nonexistent/wt", delta.work_log
    assert "role-curator" in delta.work_log[1], delta.work_log
