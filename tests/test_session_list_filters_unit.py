"""Unit tests for SessionManager.list_sessions filters (HATS-163).

Lives at the library level, below the CLI. Tests cover:

- ``role_eq`` exact match
- ``tag_filters`` AND semantics
- ``since_date`` prefix comparison (reuses session_id timestamp pattern)
- Combined filters
- Missing/corrupt metrics.json handling
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.observe import SessionManager


def _make_session_dir(
    project_dir: Path,
    session_id: str,
    *,
    metrics: dict | None = None,
) -> Path:
    """Create .gitlog/session_<id>/ with optional metrics.json."""
    sdir = project_dir / ".gitlog" / f"session_{session_id}"
    sdir.mkdir(parents=True)
    if metrics is not None:
        (sdir / "metrics.json").write_text(json.dumps(metrics))
    return sdir


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / ".gitlog").mkdir()
    return tmp_path


@pytest.fixture
def fixture_sessions(project_dir: Path):
    """A mixed set covering all filter angles."""
    _make_session_dir(project_dir, "20260401T100000Z_a1", metrics={
        "role": "diagnoser",
        "tags": {"alert_fp": "abc", "client": "home"},
        "turns": 5, "tool_calls": 10,
    })
    _make_session_dir(project_dir, "20260410T120000Z_b2", metrics={
        "role": "diagnoser",
        "tags": {"alert_fp": "xyz", "client": "home"},
        "turns": 3, "tool_calls": 7,
    })
    _make_session_dir(project_dir, "20260420T090000Z_c3", metrics={
        "role": "primary",
        "tags": {"alert_fp": "abc", "client": "work"},
        "turns": 0, "tool_calls": 0,  # unproductive
    })
    _make_session_dir(project_dir, "20260423T150000Z_d4", metrics={
        "role": "retrospector",
        # no tags field
        "turns": 1, "tool_calls": 2,
    })
    # corrupt metrics.json
    sdir = project_dir / ".gitlog" / "session_20260423T160000Z_e5"
    sdir.mkdir()
    (sdir / "metrics.json").write_text("{broken json")
    # no metrics at all
    (project_dir / ".gitlog" / "session_20260423T170000Z_f6").mkdir()


def test_no_filters_returns_all_sessions(project_dir, fixture_sessions):
    mgr = SessionManager(project_dir)
    sessions = mgr.list_sessions()
    assert len(sessions) == 6  # includes crashed/no-metrics ones


def test_role_filter_exact_match(project_dir, fixture_sessions):
    mgr = SessionManager(project_dir)
    ids = [s.session_id for s in mgr.list_sessions(role_eq="diagnoser")]
    assert ids == ["20260401T100000Z_a1", "20260410T120000Z_b2"]


def test_role_filter_skips_sessions_without_metrics(project_dir, fixture_sessions):
    """Sessions with no/corrupt metrics can't satisfy role_eq and are skipped."""
    mgr = SessionManager(project_dir)
    ids = [s.session_id for s in mgr.list_sessions(role_eq="nonexistent")]
    assert ids == []


def test_tag_filter_single_key(project_dir, fixture_sessions):
    mgr = SessionManager(project_dir)
    ids = [s.session_id for s in mgr.list_sessions(tag_filters={"alert_fp": "abc"})]
    assert ids == ["20260401T100000Z_a1", "20260420T090000Z_c3"]


def test_tag_filter_and_semantics(project_dir, fixture_sessions):
    """All k=v pairs must match — AND logic."""
    mgr = SessionManager(project_dir)
    ids = [
        s.session_id for s in mgr.list_sessions(
            tag_filters={"alert_fp": "abc", "client": "home"},
        )
    ]
    assert ids == ["20260401T100000Z_a1"]


def test_tag_filter_skips_sessions_without_tags(project_dir, fixture_sessions):
    mgr = SessionManager(project_dir)
    ids = [s.session_id for s in mgr.list_sessions(tag_filters={"alert_fp": "abc"})]
    # retrospector session has no tags field at all — excluded.
    assert "20260423T150000Z_d4" not in ids


def test_since_date_inclusive_cutoff(project_dir, fixture_sessions):
    mgr = SessionManager(project_dir)
    ids = [s.session_id for s in mgr.list_sessions(since_date="2026-04-20")]
    # >= 2026-04-20 → c3, d4, e5 (corrupt — no metrics check needed for since),
    # f6 (no metrics — since check is prefix-only, doesn't require metrics).
    assert ids == [
        "20260420T090000Z_c3",
        "20260423T150000Z_d4",
        "20260423T160000Z_e5",
        "20260423T170000Z_f6",
    ]


def test_since_date_no_matches(project_dir, fixture_sessions):
    mgr = SessionManager(project_dir)
    ids = [s.session_id for s in mgr.list_sessions(since_date="2027-01-01")]
    assert ids == []


def test_combined_filters_and(project_dir, fixture_sessions):
    """role + tag + since — all ANDed."""
    mgr = SessionManager(project_dir)
    ids = [
        s.session_id for s in mgr.list_sessions(
            role_eq="diagnoser",
            tag_filters={"client": "home"},
            since_date="2026-04-05",
        )
    ]
    # diagnoser + client=home: a1, b2 → since >= 2026-04-05 excludes a1.
    assert ids == ["20260410T120000Z_b2"]


def test_productive_only_still_works(project_dir, fixture_sessions):
    """Pre-existing filter continues to work alongside new ones."""
    mgr = SessionManager(project_dir)
    ids = [s.session_id for s in mgr.list_sessions(productive_only=True)]
    # c3 has turns=0 → excluded. e5 (corrupt) and f6 (no metrics) also out.
    assert ids == [
        "20260401T100000Z_a1",
        "20260410T120000Z_b2",
        "20260423T150000Z_d4",
    ]


def test_last_n_applies_after_filters(project_dir, fixture_sessions):
    mgr = SessionManager(project_dir)
    result = mgr.list_sessions(role_eq="diagnoser", last_n=1)
    assert [s.session_id for s in result] == ["20260410T120000Z_b2"]
