"""Pin retro.window.tasks_closed_in_window against a seeded backlog (HATS-864).

HATS-1259 ported the read onto the rack facade and swapped the window signal
from ``updated`` (re-stamped by every write) to ``completed_at`` (the terminal
transition stamp). Cards are seeded as literal yaml — no CLI in the fixture, so
the test pins the reader and not whichever writer happened to produce the card.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ai_hats.retro.window import tasks_closed_in_window

_CLOSED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_card(
    project: Path,
    task_id: str,
    *,
    state: str = "done",
    completed_at: str | None = None,
    updated: str | None = None,
) -> None:
    card: dict[str, str] = {
        "id": task_id,
        "title": f"task {task_id}",
        "state": state,
        "created": "2026-01-01",
        "updated": updated if updated is not None else _stamp(_CLOSED),
    }
    if completed_at is not None:
        card["completed_at"] = completed_at
    card_dir = ProjectLayout.at(project).tracker.tasks_dir / task_id
    card_dir.mkdir(parents=True, exist_ok=True)
    (card_dir / "task.yaml").write_text(yaml.safe_dump(card, sort_keys=False))


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def test_finds_seeded_done_task(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_card(project, "TST-1", completed_at=_stamp(_CLOSED))

    closed = tasks_closed_in_window(
        ProjectLayout.at(project), _CLOSED - timedelta(hours=1), _CLOSED + timedelta(hours=1)
    )
    assert closed == ["TST-1"]


def test_excludes_task_closed_outside_window(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_card(project, "TST-1", completed_at=_stamp(_CLOSED))

    since = _CLOSED + timedelta(hours=2)
    assert (
        tasks_closed_in_window(ProjectLayout.at(project), since, since + timedelta(hours=1)) == []
    )


def test_touching_an_old_done_card_does_not_count_as_closed(tmp_path: Path) -> None:
    """The HATS-1259 discriminator: ``updated`` inside the window is not enough.

    A card closed months ago but touched by a ``--log`` during the session
    re-stamps ``updated`` only; counting it would report work that did not happen
    in this session. Fails under a revert to the pre-HATS-1259 ``updated`` filter.
    """
    project = _project(tmp_path)
    long_ago = _CLOSED - timedelta(days=90)
    _seed_card(project, "TST-1", completed_at=_stamp(long_ago), updated=_stamp(_CLOSED))

    assert (
        tasks_closed_in_window(
            ProjectLayout.at(project), _CLOSED - timedelta(hours=1), _CLOSED + timedelta(hours=1)
        )
        == []
    )


def test_done_card_without_completed_at_warns(tmp_path: Path, caplog) -> None:
    """An unstamped ``done`` card must not vanish quietly — that is the fail-open
    class HATS-1259 exists to close, reintroduced one level down."""
    project = _project(tmp_path)
    _seed_card(project, "TST-1", completed_at=None)

    with caplog.at_level(logging.WARNING, logger="ai_hats.retro.window"):
        closed = tasks_closed_in_window(
            ProjectLayout.at(project), _CLOSED - timedelta(hours=1), _CLOSED + timedelta(hours=1)
        )

    assert closed == []
    assert "TST-1" in caplog.text


def test_ignores_cards_that_are_not_done(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_card(project, "TST-1", state="review", completed_at=_stamp(_CLOSED))

    assert (
        tasks_closed_in_window(
            ProjectLayout.at(project), _CLOSED - timedelta(hours=1), _CLOSED + timedelta(hours=1)
        )
        == []
    )


def test_project_without_a_backlog_is_empty_not_an_error(tmp_path: Path) -> None:
    """A project that never ran `ai-hats self init` has no tasks dir at all."""
    layout = ProjectLayout.at(_project(tmp_path))
    assert tasks_closed_in_window(layout, _CLOSED - timedelta(hours=1), _CLOSED) == []


def test_session_cut_with_duration_s(tmp_path: Path) -> None:
    from ai_hats.retro.window import session_cut
    from ai_hats_observe.artifacts import METRICS_JSON, session_dirname

    project = _project(tmp_path)
    sid = "20260613-191140-1"
    sdir = ProjectLayout.at(project).sessions.runs / session_dirname(sid)
    sdir.mkdir(parents=True)
    (sdir / METRICS_JSON).write_text('{"duration_s": 300}')

    cut1 = session_cut(ProjectLayout.at(project), sid)
    cut2 = session_cut(ProjectLayout.at(project), sid)
    assert cut1 == datetime(2026, 6, 13, 19, 16, 40, tzinfo=timezone.utc)
    assert cut1 == cut2


def test_session_cut_without_duration_s(tmp_path: Path) -> None:
    from ai_hats.retro.window import session_cut

    project = _project(tmp_path)
    sid = "20260613-191140-1"
    cut = session_cut(ProjectLayout.at(project), sid)
    assert cut == datetime(2026, 6, 13, 23, 59, 59, tzinfo=timezone.utc)


def test_session_cut_handles_prefixed_session_id(tmp_path: Path) -> None:
    from ai_hats.retro.window import session_cut
    from ai_hats_observe.artifacts import METRICS_JSON, session_dirname

    project = _project(tmp_path)
    sid = "20260613-191140-1"
    sdir = ProjectLayout.at(project).sessions.runs / session_dirname(sid)
    sdir.mkdir(parents=True)
    (sdir / METRICS_JSON).write_text('{"duration_s": 300}')

    cut_prefixed = session_cut(ProjectLayout.at(project), f"session_{sid}")
    cut_bare = session_cut(ProjectLayout.at(project), sid)
    assert cut_prefixed == cut_bare
    assert cut_prefixed == datetime(2026, 6, 13, 19, 16, 40, tzinfo=timezone.utc)


def test_session_cut_unparseable_session_id_returns_max(tmp_path: Path) -> None:
    from ai_hats.retro.window import session_cut

    project = _project(tmp_path)
    cut = session_cut(ProjectLayout.at(project), "x-1")
    assert cut == datetime.max.replace(tzinfo=timezone.utc)
