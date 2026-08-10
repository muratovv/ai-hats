"""HATS-1248: ``session_id`` must be unique across managers AND processes.

The id is the primary key for a session's whole on-disk footprint — the runs
dir, the cache dir, the retro file, the ownership records. Two sessions that
mint the same id silently share all of it: ``trace.log`` interleaves and
``metrics.json`` / ``audit.md`` go last-writer-wins.

The clock is pinned in every test here: "same second" is the precondition of
the defect, so it must be a fixture, never a timing accident.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ai_hats_observe.session as session_mod
from ai_hats_observe import SessionManager

_PINNED = datetime(2026, 7, 27, 12, 17, 6, tzinfo=timezone.utc)


class _FixedClock(datetime):
    """``datetime`` whose ``now()`` is frozen at :data:`_PINNED`."""

    @classmethod
    def now(cls, tz=None):  # noqa: ARG003 — signature mirrors datetime.now
        return _PINNED


def _pin_clock(monkeypatch) -> None:
    monkeypatch.setattr(session_mod, "datetime", _FixedClock)


def test_two_managers_in_one_process_mint_distinct_ids(tmp_path: Path, monkeypatch) -> None:
    """RED-under-revert: a per-instance counter restarts at 1 in each manager.

    One process really does build several managers — ``ai-hats reflect
    hypothesis`` runs two pipeline phases back to back, each constructing its
    own via ``make_session_manager`` (``cli/reflect.py:265,317``). With a
    per-instance counter both phases mint ``<TS>-1`` inside the same second.
    """
    _pin_clock(monkeypatch)
    runs = tmp_path / "runs"

    first = SessionManager(runs_dir=runs, recovery=None).create_session()
    second = SessionManager(runs_dir=runs, recovery=None).create_session()

    assert first.session_id != second.session_id, (
        f"two managers in one process minted the same id {first.session_id!r}"
    )
    assert first.session_dir != second.session_dir
    assert first.session_dir.is_dir() and second.session_dir.is_dir()


# Pins the same second in a *fresh interpreter*, then mints one id and prints it.
_MINT_IN_CHILD = """
import sys
from datetime import datetime, timezone
from pathlib import Path
import ai_hats_observe.session as S

class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 7, 27, 12, 17, 6, tzinfo=timezone.utc)

S.datetime = _FixedClock
mgr = S.SessionManager(runs_dir=Path(sys.argv[1]), recovery=None)
print(mgr.create_session().session_id)
"""


def _child_env() -> dict[str, str]:
    """Point the child at the *checkout under test*, not the installed package.

    Without this the children import whatever ``ai_hats_observe`` pip resolved —
    in a worktree that is the main checkout, so the test would silently assert
    against unfixed code and pass for the wrong reason.
    """
    import ai_hats_core

    roots = [
        str(Path(session_mod.__file__).parents[1]),
        str(Path(ai_hats_core.__file__).parents[1]),
    ]
    existing = os.environ.get("PYTHONPATH", "")
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([*roots, existing] if existing else roots),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _mint_in_subprocess(runs: Path) -> str:
    out = subprocess.run(
        [sys.executable, "-c", _MINT_IN_CHILD, str(runs)],
        capture_output=True,
        text=True,
        check=True,
        env=_child_env(),
    )
    return out.stdout.strip()


@pytest.mark.integration
def test_concurrent_processes_mint_distinct_ids(tmp_path: Path) -> None:
    """RED-under-revert: the defect itself — two processes, one second, one dir.

    Real subprocesses, because the counter is per-process state that same-process
    managers do NOT exercise. The clock is pinned identically in each child, so
    "same second" is guaranteed rather than raced for: pre-fix every child prints
    ``20260727-121706-1`` and the whole run collapses onto one session dir.

    This is the documented reproduction (two terminals, same second) and the
    upstream trigger of the HATS-604 plugin-dir shredding.
    """
    runs = tmp_path / "runs"
    n_procs = 6

    with ThreadPoolExecutor(max_workers=n_procs) as pool:
        ids = list(pool.map(lambda _: _mint_in_subprocess(runs), range(n_procs)))

    assert len(set(ids)) == n_procs, (
        f"{n_procs} processes minted {len(set(ids))} distinct ids: {sorted(ids)}"
    )
    dirs = sorted(p.name for p in runs.iterdir() if p.is_dir())
    assert len(dirs) == n_procs, f"expected {n_procs} session dirs, got {dirs}"


def test_minted_id_keeps_the_prefix_every_reader_parses(tmp_path: Path, monkeypatch) -> None:
    """The suffix must stay inert to the fixed-offset slices readers use.

    Guards the compatibility premise of HATS-1248: nothing splits or regexes a
    session id, every consumer reads a leading slice — ``[:15]`` for the
    ``%Y%m%d-%H%M%S`` start time (transcript discovery, the retro window, audit
    durations), ``[:8]`` for the date filter. This is the regression guard for
    any future reshaping of the id.
    """
    _pin_clock(monkeypatch)
    sid = SessionManager(runs_dir=tmp_path / "runs", recovery=None).create_session().session_id

    assert datetime.strptime(sid[:15], "%Y%m%d-%H%M%S") == _PINNED.replace(tzinfo=None)
    assert datetime.strptime(sid[:8], "%Y%m%d")
    assert sid[:15] + "-" == sid[:16], "the counter must still follow the timestamp"


def test_minted_id_survives_a_filename_round_trip(tmp_path: Path, monkeypatch) -> None:
    """No ``.`` or ``/`` in the id — the retro store round-trips it as a filename.

    ``retro/auto_retro.py`` writes ``sessions/<id>.md`` and reads the id back via
    ``Path(...).stem``, which truncates at the last dot. A dotted suffix would
    silently corrupt the id on the way back.
    """
    _pin_clock(monkeypatch)
    sid = SessionManager(runs_dir=tmp_path / "runs", recovery=None).create_session().session_id

    assert "." not in sid and "/" not in sid, f"id is not filename-safe: {sid!r}"
    assert Path(f"{sid}.md").stem == sid


def test_nested_id_preserves_the_parent_timestamp_prefix(tmp_path: Path, monkeypatch) -> None:
    """A child id still leads with the parent's timestamp — readers rely on it.

    Pre-existing quirk kept deliberately: ``[:15]`` on a child yields the
    *parent's* start time. Every consumer already lives with that; the suffix
    must not change which timestamp leads.
    """
    _pin_clock(monkeypatch)
    mgr = SessionManager(runs_dir=tmp_path / "runs", recovery=None)
    parent = mgr.create_session().session_id
    child = mgr.create_session(parent_session=parent).session_id

    assert child.startswith(f"{parent}_")
    assert child[:15] == parent[:15]
    assert datetime.strptime(child[:15], "%Y%m%d-%H%M%S")
