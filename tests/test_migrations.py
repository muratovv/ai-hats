"""Migration registry tests (HATS-471).

Unit-level coverage for ``ai_hats/migrations.py`` and the
``Assembler._persist_migration_step`` / init seed plumbing. The E2E gate
(``tests/e2e/test_migration_registry_gate.py``) exercises the full
subprocess + yaml round-trip.
"""

from __future__ import annotations

import pytest
import yaml

from ai_hats.assembler import Assembler
from ai_hats.migrations import MIGRATIONS, Migration, latest_step, run_pending
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


# ----- registry shape ------------------------------------------------------


def test_registry_steps_are_monotonic_and_unique():
    steps = [m.step for m in MIGRATIONS]
    assert steps == sorted(steps), "registry must be ordered by step ascending"
    assert len(set(steps)) == len(steps), "registry steps must be unique"


def test_registry_completeness_covers_all_historical_migrations():
    """Pin the six historical inline migrations are reachable via the registry.

    Updating this set is allowed when entries are intentionally added or
    deleted — but the test forces an explicit review of the registry.
    """
    expected_labels = {
        "yaml normalize (strip deprecated fields)",
        "gitignore HATS-317 cleanup",
        "obsolete files cleanup",
        "heal external refs HATS-397",
        "claude.md → v3 scaffold",
        "layout v4 (sessions+tracker+library)",
    }
    actual_labels = {m.label for m in MIGRATIONS}
    assert expected_labels.issubset(actual_labels), (
        f"missing migrations: {expected_labels - actual_labels}"
    )


def test_latest_step_equals_last_registry_entry():
    assert latest_step() == MIGRATIONS[-1].step


def test_migration_dataclass_is_frozen():
    m = MIGRATIONS[0]
    with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError
        m.step = 999  # type: ignore[misc]


# ----- runner gating -------------------------------------------------------


def _bootstrap_project(tmp_path, migration_step: int) -> Assembler:
    """Minimal project + Assembler at a specific ``migration_step``.

    Bypasses ``Assembler.init`` so tests can dial the counter directly
    without running the whole bump pipeline.
    """
    project = tmp_path / "project"
    project.mkdir()
    cfg = ProjectConfig(provider="agy", migration_step=migration_step)
    cfg.save(project / PROJECT_CONFIG)
    return Assembler(project)


def test_run_pending_skips_when_already_at_latest(tmp_path):
    asm = _bootstrap_project(tmp_path, migration_step=latest_step())
    ran = run_pending(asm)
    assert ran == 0
    assert asm.project_config.migration_step == latest_step()


def test_run_pending_advances_counter_and_persists(tmp_path, monkeypatch):
    """Each successful entry advances ``migration_step`` and persists to yaml."""
    asm = _bootstrap_project(tmp_path, migration_step=0)

    # Replace every registry entry with a no-op to isolate the runner's
    # bookkeeping from the actual migration bodies.
    noop_migrations = [
        Migration(step=m.step, run=lambda _a: None, label=m.label)
        for m in MIGRATIONS
    ]
    monkeypatch.setattr("ai_hats.migrations.MIGRATIONS", noop_migrations)

    ran = run_pending(asm)
    assert ran == len(MIGRATIONS)
    assert asm.project_config.migration_step == latest_step()

    # Persistence: yaml on disk holds the final step.
    on_disk = yaml.safe_load(asm.config_path.read_text())
    assert on_disk["migration_step"] == latest_step()


def test_run_pending_partial_failure_persists_last_good_step(tmp_path, monkeypatch):
    """Entry that raises mid-registry leaves counter at the previous successful step."""
    asm = _bootstrap_project(tmp_path, migration_step=0)

    def boom(_assembler) -> None:
        raise RuntimeError("simulated failure")

    # First two entries succeed, third raises, last three never reached.
    rigged = [
        Migration(step=1, run=lambda _a: None, label="ok-1"),
        Migration(step=2, run=lambda _a: None, label="ok-2"),
        Migration(step=3, run=boom, label="fails"),
        Migration(step=4, run=lambda _a: None, label="never-reached-1"),
        Migration(step=5, run=lambda _a: None, label="never-reached-2"),
    ]
    monkeypatch.setattr("ai_hats.migrations.MIGRATIONS", rigged)

    with pytest.raises(RuntimeError, match="simulated failure"):
        run_pending(asm)

    # In-memory counter
    assert asm.project_config.migration_step == 2
    # Persisted counter (on disk) matches in-memory — confirms persist-after-each.
    on_disk = yaml.safe_load(asm.config_path.read_text())
    assert on_disk["migration_step"] == 2


def test_run_pending_rolls_back_in_memory_step_when_persist_fails(
    tmp_path, monkeypatch,
):
    """Transactional contract: if ``_persist_migration_step`` raises after
    a migration succeeded, the in-memory ``cfg.migration_step`` MUST NOT
    advance. Without rollback, callers reading ``cfg.migration_step``
    after a failed bump would see a step number that does not exist on
    disk, defeating the partial-failure resume guarantee.
    """
    asm = _bootstrap_project(tmp_path, migration_step=0)

    rigged = [
        Migration(step=1, run=lambda _a: None, label="ok-1"),
        Migration(step=2, run=lambda _a: None, label="persist-fails-here"),
    ]
    monkeypatch.setattr("ai_hats.migrations.MIGRATIONS", rigged)

    # Make persistence fail on the second entry. First call (step=1) is
    # allowed; second (step=2) raises.
    real_persist = asm._persist_migration_step
    call_log: list[int] = []

    def flaky_persist(step: int) -> None:
        call_log.append(step)
        if step == 2:
            raise OSError("simulated disk failure")
        real_persist(step)

    monkeypatch.setattr(asm, "_persist_migration_step", flaky_persist)

    with pytest.raises(OSError, match="simulated disk failure"):
        run_pending(asm)

    # Step 1 succeeded fully (both ran and persisted).
    # Step 2 ran but persist failed → in-memory MUST roll back to 1.
    assert asm.project_config.migration_step == 1, (
        f"in-memory migration_step={asm.project_config.migration_step}; "
        f"expected 1 (rollback after persist failure on step 2)"
    )
    on_disk = yaml.safe_load(asm.config_path.read_text())
    assert on_disk["migration_step"] == 1
    # Sanity: persist was actually called for step 2 (the failure path
    # we wanted to exercise), not skipped over.
    assert call_log == [1, 2]


def test_run_pending_resumes_from_persisted_step(tmp_path, monkeypatch):
    """After a partial failure, a subsequent ``run_pending`` resumes
    at the failed entry, not from zero."""
    asm = _bootstrap_project(tmp_path, migration_step=2)

    executed: list[int] = []
    rigged = [
        Migration(step=1, run=lambda _a: executed.append(1), label="should-skip"),
        Migration(step=2, run=lambda _a: executed.append(2), label="should-skip"),
        Migration(step=3, run=lambda _a: executed.append(3), label="resume-here"),
        Migration(step=4, run=lambda _a: executed.append(4), label="continue"),
    ]
    monkeypatch.setattr("ai_hats.migrations.MIGRATIONS", rigged)

    ran = run_pending(asm)
    assert ran == 2
    assert executed == [3, 4]  # only entries past the persisted step
    assert asm.project_config.migration_step == 4


def test_run_pending_emits_stable_banner_to_stderr(tmp_path, monkeypatch, capsys):
    """The ``[ai-hats] running migration step=N label=...`` banner is the
    E2E gate's spy contract. Pinning it here so a refactor that drops or
    renames the prefix fails fast in CI rather than silently breaking the
    integration test.
    """
    asm = _bootstrap_project(tmp_path, migration_step=0)

    rigged = [Migration(step=1, run=lambda _a: None, label="probe-label")]
    monkeypatch.setattr("ai_hats.migrations.MIGRATIONS", rigged)

    run_pending(asm)
    captured = capsys.readouterr()

    assert "[ai-hats] running migration step=1" in captured.err
    assert "probe-label" in captured.err


def test_run_pending_emits_via_logger_too(tmp_path, monkeypatch, caplog):
    """The banner is dual-channel: ``print(file=sys.stderr)`` covers
    subprocess visibility, ``logger.info`` covers structured callers that
    capture via the ``logging`` framework. If a future refactor drops one
    channel, the other survives — without this test, the regression goes
    unnoticed until someone wires a log aggregator and finds nothing.
    """
    import logging

    asm = _bootstrap_project(tmp_path, migration_step=0)
    rigged = [Migration(step=1, run=lambda _a: None, label="probe-label")]
    monkeypatch.setattr("ai_hats.migrations.MIGRATIONS", rigged)

    with caplog.at_level(logging.INFO, logger="ai_hats.migrations"):
        run_pending(asm)

    assert any(
        "probe-label" in rec.message and "step=1" in rec.message
        for rec in caplog.records
    ), f"logger.info channel missing 'probe-label'; got: {[r.message for r in caplog.records]}"


def test_run_pending_does_not_emit_banner_when_gated(tmp_path, monkeypatch, capsys):
    """The mirror invariant for the E2E gate: when the registry has nothing
    to do, the banner MUST NOT appear. A regression that drops the gate
    (e.g. the ``if step >= m.step: continue`` short-circuit) would surface
    here too.
    """
    asm = _bootstrap_project(tmp_path, migration_step=latest_step())

    run_pending(asm)
    captured = capsys.readouterr()

    assert "[ai-hats] running migration" not in captured.err


# ----- ProjectConfig.migration_step field ----------------------------------


def test_migration_step_defaults_to_zero():
    """Backward-compat: existing yaml without ``migration_step`` loads at 0.
    This is the seed value the registry replays from."""
    cfg = ProjectConfig(provider="agy")
    assert cfg.migration_step == 0


def test_migration_step_round_trips_through_yaml(tmp_path):
    path = tmp_path / PROJECT_CONFIG
    cfg = ProjectConfig(provider="agy", migration_step=4)
    cfg.save(path)

    raw = yaml.safe_load(path.read_text())
    assert raw["migration_step"] == 4

    loaded = ProjectConfig.from_yaml(path)
    assert loaded.migration_step == 4


# ----- init seed behaviour -------------------------------------------------


def test_init_seeds_migration_step_to_latest_for_greenfield(tmp_path):
    """A brand-new project seeded by ``Assembler.init`` starts at
    ``latest_step()`` — the registry is a no-op on the very first bump.
    """
    project = tmp_path / "fresh"
    project.mkdir()
    asm = Assembler(project)
    asm.init()

    cfg = ProjectConfig.from_yaml(project / PROJECT_CONFIG)
    assert cfg.migration_step == latest_step()


def test_existing_project_without_migration_step_seeds_to_zero(tmp_path):
    """An existing v4 project upgraded to this release loads with
    ``migration_step=0`` (pydantic default). The next bump will replay
    the full registry once; all entries are idempotent by contract."""
    project = tmp_path / "existing"
    project.mkdir()
    # Hand-craft a v4 yaml WITHOUT migration_step (representing an
    # upgrade from a release that pre-dates HATS-471).
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "provider: agy\n"
    )

    cfg = ProjectConfig.from_yaml(project / PROJECT_CONFIG)
    assert cfg.migration_step == 0  # seeds the registry replay
