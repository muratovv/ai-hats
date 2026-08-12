"""HATS-1593 — the budget primitive: absolute, monotonic, shrink-only."""

from __future__ import annotations

import pytest

from ai_hats_core.deadline import Deadline


def test_under_lock_names_the_lock_that_bounds_it():
    d = Deadline.under_lock(60.0, lock="wt lifecycle")
    assert "wt lifecycle" in d.origin
    assert "60s" in d.origin


def test_without_lock_declares_itself_unlocked():
    d = Deadline.without_lock(30.0, why="session startup")
    assert d.origin.startswith("unlocked:")
    assert "session startup" in d.origin


def test_remaining_shrinks_as_the_clock_advances(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr("ai_hats_core.deadline.time.monotonic", lambda: clock["now"])
    d = Deadline.under_lock(60.0, lock="wt lifecycle")

    assert d.remaining() == pytest.approx(60.0)
    clock["now"] += 25.0
    assert d.remaining() == pytest.approx(35.0)


def test_remaining_floors_at_zero_and_reports_expired(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr("ai_hats_core.deadline.time.monotonic", lambda: clock["now"])
    d = Deadline.under_lock(10.0, lock="wt create")

    clock["now"] += 999.0
    assert d.remaining() == 0.0
    assert d.expired()


def test_budget_for_caps_a_request_that_outlives_the_lock(monkeypatch):
    """The wt:create defect in one assertion: a 45s hook under a 10s lock."""
    monkeypatch.setattr("ai_hats_core.deadline.time.monotonic", lambda: 1000.0)
    d = Deadline.under_lock(10.0, lock="wt create")

    assert d.budget_for(45.0) == pytest.approx(10.0)


def test_budget_for_never_lengthens_a_smaller_request(monkeypatch):
    monkeypatch.setattr("ai_hats_core.deadline.time.monotonic", lambda: 1000.0)
    d = Deadline.under_lock(60.0, lock="wt lifecycle")

    assert d.budget_for(45.0) == pytest.approx(45.0)


def test_successive_draws_share_one_ceiling(monkeypatch):
    """N hooks in a loop: the aggregate stays inside the lock, not N x budget."""
    clock = {"now": 1000.0}
    monkeypatch.setattr("ai_hats_core.deadline.time.monotonic", lambda: clock["now"])
    d = Deadline.under_lock(60.0, lock="wt lifecycle")

    first = d.budget_for(45.0)
    assert first == pytest.approx(45.0)
    clock["now"] += first  # the first hook spent its whole budget

    assert d.budget_for(45.0) == pytest.approx(15.0)
    clock["now"] += 15.0
    assert d.budget_for(45.0) == 0.0


def test_clamped_to_yields_the_outer_deadline_when_it_expires_first():
    """HATS-1603: a 60s lock taken inside a 30s one cannot outlive the 30s."""
    outer = Deadline.under_lock(30.0, lock="rack task")
    own = Deadline.under_lock(60.0, lock="wt lifecycle")

    clamped = own.clamped_to(outer)

    assert clamped.expires_at == pytest.approx(outer.expires_at)
    assert "rack task" in clamped.origin  # the binding constraint names itself


def test_clamped_to_keeps_its_own_deadline_when_it_expires_first():
    outer = Deadline.under_lock(120.0, lock="rack task")
    own = Deadline.under_lock(60.0, lock="wt lifecycle")

    clamped = own.clamped_to(outer)

    assert clamped.expires_at == pytest.approx(own.expires_at)
    assert "wt lifecycle" in clamped.origin


def test_clamped_to_none_is_the_unnested_road():
    """``ai-hats wt merge`` direct: no enclosing lock, so nothing to clamp."""
    own = Deadline.under_lock(60.0, lock="wt lifecycle")

    assert own.clamped_to(None) is own


def test_clamped_to_never_outlives_either_side():
    own = Deadline.under_lock(60.0, lock="wt lifecycle")
    for outer_timeout in (1.0, 30.0, 59.9, 60.0, 60.1, 600.0):
        outer = Deadline.under_lock(outer_timeout, lock="rack task")

        clamped = own.clamped_to(outer)

        assert clamped.expires_at <= own.expires_at
        assert clamped.expires_at <= outer.expires_at


def test_is_immutable():
    d = Deadline.under_lock(60.0, lock="wt lifecycle")
    with pytest.raises(Exception):
        d.expires_at = 0.0  # type: ignore[misc]
