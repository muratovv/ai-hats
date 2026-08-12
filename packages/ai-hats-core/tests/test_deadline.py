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


def test_is_immutable():
    d = Deadline.under_lock(60.0, lock="wt lifecycle")
    with pytest.raises(Exception):
        d.expires_at = 0.0  # type: ignore[misc]
