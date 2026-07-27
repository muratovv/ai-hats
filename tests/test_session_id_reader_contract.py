"""HATS-1248: a minted session_id stays readable by every integrator consumer.

The uniqueness suffix is only safe because no reader parses past the timestamp
prefix. That premise is asserted here against the REAL readers rather than a
restated slice, so a future reshaping of the id fails on the consumer that would
actually break — not on a copy of its assumptions.

Lives in the root suite because ``ai_hats_observe`` may not import the
integrator (ADR-0014); the shape-only half lives in the observe package.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ai_hats_observe import SessionManager
from ai_hats_observe.artifacts import session_dirname, strip_session_prefix

from ai_hats.paths._discovery import session_start_ts
from ai_hats.retro.window import parse_session_start

_PINNED = datetime(2026, 7, 27, 12, 17, 6, tzinfo=timezone.utc)


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ARG003 — signature mirrors datetime.now
        return _PINNED


def _mint(tmp_path: Path, monkeypatch, *, parent: str | None = None) -> str:
    import ai_hats_observe.session as session_mod

    monkeypatch.setattr(session_mod, "datetime", _FixedClock)
    mgr = SessionManager(runs_dir=tmp_path / "runs", recovery=None)
    return mgr.create_session(parent_session=parent).session_id


def test_transcript_discovery_reads_the_start_time(tmp_path: Path, monkeypatch) -> None:
    """``session_start_ts`` is the mtime floor for transcript discovery.

    It swallows errors and returns None, so a shape break here degrades silently
    into "transcript never found" — the highest-blast-radius reader.
    """
    ts = session_start_ts(_mint(tmp_path, monkeypatch))

    assert ts is not None, "session_start_ts returned None for a freshly minted id"
    assert ts == _PINNED.timestamp()


def test_retro_window_reads_the_start_time(tmp_path: Path, monkeypatch) -> None:
    """``parse_session_start`` is the only reader that raises rather than swallow."""
    assert parse_session_start(_mint(tmp_path, monkeypatch)) == _PINNED


def test_readers_accept_the_prefixed_dirname_form(tmp_path: Path, monkeypatch) -> None:
    """The id also reaches readers as the ``session_<id>`` directory name."""
    sid = _mint(tmp_path, monkeypatch)

    assert strip_session_prefix(session_dirname(sid)) == sid
    assert parse_session_start(session_dirname(sid)) == _PINNED


def test_readers_accept_a_nested_id(tmp_path: Path, monkeypatch) -> None:
    """A child id leads with the parent's timestamp — readers must still parse it."""
    parent = _mint(tmp_path, monkeypatch)
    child = _mint(tmp_path, monkeypatch, parent=parent)

    assert session_start_ts(child) == _PINNED.timestamp()
    assert parse_session_start(child) == _PINNED
