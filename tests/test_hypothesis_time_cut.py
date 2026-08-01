"""Unit tests for created_at_or_before time cutoff (HATS-1445)."""

from datetime import datetime, timezone
from dataclasses import dataclass

from ai_hats.rack_workspace import created_at_or_before


@dataclass(frozen=True)
class DummyView:
    id: str
    created: str = ""


def test_date_only_on_session_day_is_kept() -> None:
    cut = datetime(2026, 6, 13, 19, 11, 40, tzinfo=timezone.utc)
    views = [DummyView(id="HYP-1", created="2026-06-13")]
    assert created_at_or_before(views, cut) == views


def test_iso_timestamp_after_session_cut_is_filtered() -> None:
    cut = datetime(2026, 6, 13, 19, 11, 40, tzinfo=timezone.utc)
    views = [
        DummyView(id="HYP-1", created="2026-06-13T18:00:00Z"),
        DummyView(id="HYP-2", created="2026-06-13T20:00:00Z"),
    ]
    kept = created_at_or_before(views, cut)
    assert [v.id for v in kept] == ["HYP-1"]


def test_iso_timestamp_before_session_cut_is_kept() -> None:
    cut = datetime(2026, 6, 13, 19, 11, 40, tzinfo=timezone.utc)
    views = [DummyView(id="HYP-1", created="2026-06-12T10:00:00+00:00")]
    assert created_at_or_before(views, cut) == views


def test_missing_or_unparseable_created_is_kept_fail_open() -> None:
    cut = datetime(2026, 6, 13, 19, 11, 40, tzinfo=timezone.utc)
    views = [
        DummyView(id="HYP-1", created=""),
        DummyView(id="HYP-2", created="invalid-date"),
    ]
    assert created_at_or_before(views, cut) == views
