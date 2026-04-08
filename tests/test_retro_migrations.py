"""Tests for retro migration registry — walker, cycle detection, dummy v2."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from ai_hats.retro import migrations as m


@contextmanager
def _temporary_migration(
    from_v: str, to_v: str, fn
) -> Iterator[None]:
    """Register a migration for the duration of the test, then remove it."""
    key = (from_v, to_v)
    assert key not in m.MIGRATIONS, f"Migration {from_v} → {to_v} already registered"
    m.MIGRATIONS[key] = fn
    try:
        yield
    finally:
        m.MIGRATIONS.pop(key, None)


@contextmanager
def _temporary_latest(family: str, version: str) -> Iterator[None]:
    saved = m.LATEST_VERSIONS[family]
    m.LATEST_VERSIONS[family] = version
    try:
        yield
    finally:
        m.LATEST_VERSIONS[family] = saved


# --- happy paths ---


def test_passthrough_when_already_at_latest() -> None:
    out = m.migrate_to_latest({"schema": "hats-session-retro/v1", "x": 1})
    assert out["schema"] == "hats-session-retro/v1"
    assert out["x"] == 1


def test_family_of_extracts_prefix() -> None:
    assert m.family_of("hats-session-retro/v1") == "hats-session-retro"
    assert m.family_of("hats-bundle/v42") == "hats-bundle"


def test_family_of_rejects_invalid_format() -> None:
    with pytest.raises(ValueError, match="family/vN"):
        m.family_of("no-slash-here")


# --- failure modes ---


def test_missing_schema_field_raises() -> None:
    with pytest.raises(ValueError, match="missing required 'schema'"):
        m.migrate_to_latest({"x": 1})


def test_non_string_schema_raises() -> None:
    with pytest.raises(ValueError, match="must be a string"):
        m.migrate_to_latest({"schema": 42})


def test_unknown_family_raises() -> None:
    with pytest.raises(ValueError, match="Unknown schema family"):
        m.migrate_to_latest({"schema": "hats-mystery/v1"})


def test_no_migration_path_raises() -> None:
    """An old version that has no migration registered should error."""
    with _temporary_latest("hats-session-retro", "hats-session-retro/v999"):
        with pytest.raises(ValueError, match="No migration path"):
            m.migrate_to_latest({"schema": "hats-session-retro/v1"})


def test_register_rejects_duplicate() -> None:
    def noop(d):
        return d

    with _temporary_migration("hats-session-retro/v1", "hats-session-retro/v999", noop):
        with pytest.raises(ValueError, match="Duplicate migration"):
            m.register("hats-session-retro/v1", "hats-session-retro/v999")(noop)


def test_register_rejects_cross_family() -> None:
    with pytest.raises(ValueError, match="Cross-family"):
        @m.register("hats-session-retro/v1", "hats-bundle/v1")
        def cross(d):
            return d


# --- dummy v1 → v2 migration: proves the mechanism end-to-end ---


def test_dummy_migration_v1_to_v2_chains_correctly() -> None:
    """Register a fake v1→v2 migration, run it, verify output and side effects."""

    def v1_to_v2(d: dict) -> dict:
        d["schema"] = "hats-session-retro/v2"
        d["new_field"] = "added by migration"
        return d

    with _temporary_migration("hats-session-retro/v1", "hats-session-retro/v2", v1_to_v2):
        with _temporary_latest("hats-session-retro", "hats-session-retro/v2"):
            out = m.migrate_to_latest({
                "schema": "hats-session-retro/v1",
                "session_id": "x",
            })
            assert out["schema"] == "hats-session-retro/v2"
            assert out["new_field"] == "added by migration"
            assert out["session_id"] == "x"


def test_dummy_migration_v1_to_v2_to_v3_chains_two_steps() -> None:
    """Two-step chain: v1 → v2 → v3, verify walker visits both."""

    def v1_to_v2(d: dict) -> dict:
        d["schema"] = "hats-session-retro/v2"
        d["step1"] = True
        return d

    def v2_to_v3(d: dict) -> dict:
        d["schema"] = "hats-session-retro/v3"
        d["step2"] = True
        return d

    with _temporary_migration("hats-session-retro/v1", "hats-session-retro/v2", v1_to_v2):
        with _temporary_migration("hats-session-retro/v2", "hats-session-retro/v3", v2_to_v3):
            with _temporary_latest("hats-session-retro", "hats-session-retro/v3"):
                out = m.migrate_to_latest({"schema": "hats-session-retro/v1"})
                assert out["step1"] is True
                assert out["step2"] is True
                assert out["schema"] == "hats-session-retro/v3"


def test_cycle_detected() -> None:
    """A migration that produces a previously-visited version triggers cycle error."""

    def v1_to_v2(d: dict) -> dict:
        d["schema"] = "hats-session-retro/v2"
        return d

    def v2_back_to_v1(d: dict) -> dict:
        d["schema"] = "hats-session-retro/v1"
        return d

    with _temporary_migration("hats-session-retro/v1", "hats-session-retro/v2", v1_to_v2):
        with _temporary_migration("hats-session-retro/v2", "hats-session-retro/v1", v2_back_to_v1):
            with _temporary_latest("hats-session-retro", "hats-session-retro/vNEVER"):
                with pytest.raises(ValueError, match="cycle detected"):
                    m.migrate_to_latest({"schema": "hats-session-retro/v1"})


def test_migration_must_set_schema_field() -> None:
    """A migration that forgets to update 'schema' is rejected."""

    def buggy(d: dict) -> dict:
        d.pop("schema", None)  # forget to set it
        return d

    with _temporary_migration("hats-session-retro/v1", "hats-session-retro/v2", buggy):
        with _temporary_latest("hats-session-retro", "hats-session-retro/v2"):
            with pytest.raises(ValueError, match="did not set"):
                m.migrate_to_latest({"schema": "hats-session-retro/v1"})


def test_migration_does_not_mutate_input() -> None:
    """migrate_to_latest should not mutate the caller's dict."""

    def v1_to_v2(d: dict) -> dict:
        d["schema"] = "hats-session-retro/v2"
        d["mutated"] = True
        return d

    original = {"schema": "hats-session-retro/v1", "untouched": True}
    with _temporary_migration("hats-session-retro/v1", "hats-session-retro/v2", v1_to_v2):
        with _temporary_latest("hats-session-retro", "hats-session-retro/v2"):
            out = m.migrate_to_latest(original)

    assert "mutated" not in original
    assert original["schema"] == "hats-session-retro/v1"
    assert out["mutated"] is True
