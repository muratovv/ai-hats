"""HATS-1735 — the seam three readers share, and the one policy source under it.

The seam is a leaf so ``wt_effects`` can reach it without importing
``rack_wiring`` (that edge is a cycle), and it answers a four-valued verdict so
"no session" cannot arrive at a call site looking like "refused".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.consent_port import APP, Operation, Outcome, declared_types, record_use, verdict
from ai_hats.session_identity import SessionIdentity
from ai_hats_library.hooks.consent_gate import Radius, issue, store_root_from

MOVE = Operation("rack.transition", subject="HATS-1735", label="→ execute")


def _materialization(session_dir: Path, rows: list[dict]) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "role_materialization.json").write_text(
        json.dumps({"role": "maintainer", "checks": [], "consent": rows}), encoding="utf-8"
    )


def _row(app: str, selector: str, *, path: tuple[str, ...] = ()) -> dict:
    return {"app": app, "path": list(path), "selector": selector, "declared_by": "trait-agent"}


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A session as a launch would leave it: envelope in env, declaration on disk."""
    project = tmp_path / "proj"
    project.mkdir()
    session_dir = tmp_path / "runs" / "session_sid-1"
    cache_dir = tmp_path / "cache" / "sid-1"
    _materialization(
        session_dir,
        [_row(APP, "review->done", path=("rack.transition",))],
    )
    identity = SessionIdentity(
        id="sid-1",
        role="maintainer",
        provider="claude",
        project_dir=project,
        session_dir=session_dir,
        session_cache_dir=str(cache_dir),
    )
    for key, value in identity.to_env().items():
        monkeypatch.setenv(key, value)
    return project, cache_dir


def test_only_the_consent_gate_operation_paths_become_policy(tmp_path: Path):
    session_dir = tmp_path / "s"
    _materialization(
        session_dir,
        [
            _row(APP, "pre-merge", path=("wt.merge",)),
            _row("rack", "plan->execute"),
        ],
    )

    assert declared_types(session_dir) == ("wt.merge",)


def test_a_declaration_that_cannot_be_read_is_an_empty_policy(tmp_path: Path):
    assert declared_types(tmp_path / "nowhere") == ()


def test_outside_a_session_the_answer_is_no_agent_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("AI_HATS_SESSION_IDENTITY", raising=False)
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)

    assert verdict(MOVE, target_dir=tmp_path).outcome is Outcome.NO_AGENT


def test_a_grant_written_for_this_session_is_seen_through_the_seam(session):
    project, cache_dir = session
    issue(
        Radius(types=("rack.transition",)),
        store_root=store_root_from(cache_dir),
        session_id="sid-1",
        project_dir=project,
    )

    assert verdict(MOVE, target_dir=project).outcome is Outcome.GRANTED


def test_the_same_grant_does_not_reach_another_project(session, tmp_path: Path):
    project, cache_dir = session
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    issue(
        Radius(types=("rack.transition",)),
        store_root=store_root_from(cache_dir),
        session_id="sid-1",
        project_dir=project,
    )

    assert verdict(MOVE, target_dir=elsewhere).outcome is Outcome.DENIED


def test_an_undeclared_type_is_not_covered_however_wide_the_grant(session):
    project, cache_dir = session
    issue(
        Radius(types=("*",)),
        store_root=store_root_from(cache_dir),
        session_id="sid-1",
        project_dir=project,
    )
    merge = Operation("wt.merge", subject="task/hats-1735")

    assert verdict(merge, target_dir=project).outcome is Outcome.DENIED


def test_record_use_is_anchored_to_the_explicit_project(session, capsys):
    project, cache_dir = session
    grant = issue(
        Radius(types=("rack.transition",)),
        store_root=store_root_from(cache_dir),
        session_id="sid-1",
        project_dir=project,
    )
    answer = verdict(MOVE, target_dir=project)

    record_use(answer, MOVE, hook="test", project_dir=project)

    said = capsys.readouterr().err
    assert grant.id[:8] in said
    assert "no git dir" in said


def test_the_record_the_engine_hands_us_names_radius_window_and_outcome():
    from ai_hats.consent_port import journal_reason

    line = journal_reason(
        {
            "grant_id": "6a3b0f21deadbeef",
            "op": "rack.transition",
            "radius": ["rack.transition", "wt.merge"],
            "window_s": 1800,
            "left_s": 1200,
            "outcome": "granted",
        }
    )

    assert line.startswith("consent grant 6a3b0f21 (rack.transition)"), line
    assert "radius=[rack.transition,wt.merge]" in line, line
    assert "window=20m/30m" in line, line
    assert "outcome=granted" in line, line


def test_an_unreadable_envelope_is_no_agent_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The D6 collapse this whole engine exists to forbid, on the seam's own
    error path: a malformed envelope means "I could not look", never "refused"."""
    monkeypatch.setenv("AI_HATS_SESSION_IDENTITY", "{not json at all")

    answer = verdict(MOVE, target_dir=tmp_path)

    assert answer.outcome is Outcome.NO_AGENT, answer
    assert "envelope" in answer.reason, answer.reason
