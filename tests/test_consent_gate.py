"""HATS-1735 — the grant: one answer covering a series of moves for a named window.

Behaviour, never a constant asserted against itself: the window is 120 minutes
because a grant issued with no number expires two hours out, and the outcome set
is closed because "I could not look" comes back as its own member rather than as
a refusal.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from ai_hats_library.hooks.consent_gate import (
    DEFAULT_WINDOW_MINUTES,
    IssueError,
    Operation,
    Outcome,
    Radius,
    check,
    grants_dir,
    issue,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
#: The shipped engine, named by path so a move fails here and not in a hook.
ENGINE = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/hooks/consent_gate"

POLICY = ("rack.transition", "wt.merge")
NOW = 1_000_000.0
MOVE = Operation("rack.transition", subject="HATS-1735", params={"to": "execute"})


def test_the_shipped_engine_is_where_the_hook_expects_it():
    assert (ENGINE / "check.py").is_file(), f"the checking half moved: {ENGINE}"
    assert (ENGINE / "issue.py").is_file(), f"the issuing half moved: {ENGINE}"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "sessions" / "sid-1" / "consent"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    anchor = tmp_path / "proj"
    anchor.mkdir()
    return anchor


def _issue(store: Path, project: Path, *, types=("rack.transition",), **kw):
    return issue(
        Radius(types=types, subjects=kw.pop("subjects", ("*",))),
        store_root=store,
        session_id=kw.pop("session_id", "sid-1"),
        project_dir=kw.pop("project_dir", project),
        now=kw.pop("now", NOW),
        **kw,
    )


def _check(store: Path, project: Path, *, op=MOVE, now=NOW, **kw):
    return check(
        op,
        session_id=kw.pop("session_id", "sid-1"),
        store_root=kw.pop("store_root", store),
        project_dir=kw.pop("project_dir", project),
        policy=kw.pop("policy", POLICY),
        now=now,
    )


# --- the four outcomes, each reachable and each distinct -----------------------


def test_a_live_grant_covers_a_move_of_the_same_type(store, project):
    _issue(store, project)
    assert _check(store, project).outcome is Outcome.GRANTED


def test_a_second_different_move_inside_the_window_is_covered_too(store, project):
    """The whole point: one answer, a SERIES of moves."""
    _issue(store, project)
    later = Operation("rack.transition", subject="HATS-9999", params={"to": "done"})
    assert _check(store, project, op=later, now=NOW + 600).outcome is Outcome.GRANTED


def test_an_expired_window_stops_covering(store, project):
    _issue(store, project, minutes=30)
    assert _check(store, project, now=NOW + 31 * 60).outcome is Outcome.DENIED


def test_no_envelope_is_not_a_refusal(store, project):
    """`NO_AGENT` must never collapse into `DENIED` (ADR-0029 D6)."""
    assert _check(store, project, session_id="").outcome is Outcome.NO_AGENT
    assert _check(store, project, store_root=None).outcome is Outcome.NO_AGENT
    assert _check(store, project, project_dir=None).outcome is Outcome.NO_AGENT


def test_a_missing_store_is_no_grant_not_an_error(store, project):
    """P5: an unreadable store reads as absence, or the fallback never runs."""
    assert _check(store, project).outcome is Outcome.DENIED


# --- the three axes the grant is bound on -------------------------------------


def test_another_sessions_grant_is_invisible(store, project):
    """Restart IS the revocation (ADR-0029 D9) — held by the binding, not by luck."""
    _issue(store, project, session_id="sid-OTHER")
    assert _check(store, project).outcome is Outcome.DENIED


def test_a_grant_does_not_reach_another_project(store, project, tmp_path):
    elsewhere = tmp_path / "other-repo"
    elsewhere.mkdir()
    _issue(store, project)
    assert _check(store, project, project_dir=elsewhere).outcome is Outcome.DENIED


def test_a_radius_naming_another_type_does_not_cover_this_one(store, project):
    _issue(store, project, types=("wt.merge",))
    verdict = _check(store, project)
    assert verdict.outcome is Outcome.DENIED
    assert "wt.merge" in verdict.reason, "the refusal must say a grant exists, but not this one"


def test_a_subject_radius_covers_only_that_subject(store, project):
    _issue(store, project, subjects=("HATS-1735",))
    other = Operation("rack.transition", subject="HATS-0001")
    assert _check(store, project).outcome is Outcome.GRANTED
    assert _check(store, project, op=other).outcome is Outcome.DENIED


# --- policy: the engine never learns what rack is ------------------------------


def test_an_undeclared_type_is_refused_and_the_declared_set_is_named(store, project):
    _issue(store, project, types=("*",))
    verdict = _check(store, project, policy=("wt.merge",))
    assert verdict.outcome is Outcome.DENIED
    assert "wt.merge" in verdict.reason


# --- the collapse D6 forbids ---------------------------------------------------


def test_a_verdict_cannot_be_truth_tested(store, project):
    verdict = _check(store, project)
    with pytest.raises(TypeError, match="four outcomes"):
        bool(verdict)


# --- issuing -------------------------------------------------------------------


def test_a_grant_issued_with_no_number_lives_the_default_window(store, project):
    grant = _issue(store, project)
    assert grant.expires_at - grant.issued_at == DEFAULT_WINDOW_MINUTES * 60


def test_a_grant_is_written_unreadable_to_anyone_else(store, project):
    grant = _issue(store, project)
    mode = stat.S_IMODE(grant.path.stat().st_mode)
    assert mode == 0o600, f"grant is readable beyond its owner: {oct(mode)}"
    assert stat.S_IMODE(grants_dir(store).stat().st_mode) == 0o700


def test_an_empty_radius_is_refused_rather_than_written(store, project):
    with pytest.raises(IssueError):
        _issue(store, project, types=())
    assert not grants_dir(store).exists()


def test_a_window_already_over_is_refused(store, project):
    with pytest.raises(IssueError):
        _issue(store, project, minutes=0)


def test_the_grant_names_the_project_it_was_issued_for(store, project):
    grant = _issue(store, project)
    written = json.loads(grant.path.read_text(encoding="utf-8"))
    assert written["project_dir"] == str(project)
    assert written["session_id"] == "sid-1"


def test_two_grants_never_collide(store, project):
    first = _issue(store, project)
    second = _issue(store, project, types=("wt.merge",))
    assert first.path != second.path
    assert len(list(grants_dir(store).iterdir())) == 2


def test_a_corrupt_grant_file_is_ignored_not_fatal(store, project):
    _issue(store, project)
    (grants_dir(store) / "junk.json").write_text("{not json", encoding="utf-8")
    assert _check(store, project).outcome is Outcome.GRANTED


def test_a_grant_from_another_format_version_is_ignored(store, project):
    grant = _issue(store, project)
    data = json.loads(grant.path.read_text(encoding="utf-8"))
    data["v"] = 999
    grant.path.write_text(json.dumps(data), encoding="utf-8")
    assert _check(store, project).outcome is Outcome.DENIED
