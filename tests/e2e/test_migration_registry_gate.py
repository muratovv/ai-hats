"""E2E: HATS-471 migration registry gate.

Four contracts a reviewer can refute by reverting the relevant code:

1. **First-bump replay.** A yaml with ``migration_step`` absent (existing
   project upgrading to HATS-471) seeds the counter to 0, the registry
   replay banner appears on stderr, and the yaml is rewritten with
   ``migration_step = latest``.
2. **Gating.** A second ``bump`` on the migrated yaml emits NO registry
   banner — every entry is short-circuited by ``cfg.migration_step >=
   m.step``. Failure-under-revert: dropping the ``continue`` short-circuit
   in :func:`ai_hats.migrations.run_pending` re-fires every entry, the
   banner appears, and this test fails.
3. **Greenfield init seed.** ``ai-hats self init`` writes
   ``migration_step = latest`` into the freshly-created yaml without
   ever running the registry (no banner on init stderr either).
4. **Partial-failure persistence is a unit contract** (see
   ``tests/test_migrations.py``), not exercised here — the e2e gate
   focuses on the user-observable subprocess boundary.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.

Cost amortization (HATS-582): reuses the session-scoped shared venv via
:func:`tests.e2e.conftest.shared_launcher` — no per-module venv build.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BANNER_PREFIX = "[ai-hats] running migration"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.fixture
def installed_launcher(shared_launcher):
    """Delegate to the session-scoped shared venv (HATS-582).

    Was a module-scoped builder (~90s) — now reuses the single session venv
    from :func:`tests.e2e.conftest.shared_launcher`. Every test here is
    read-only on the venv (works in a fresh ``tmp_path`` project). Returns
    the same ``(launcher, env, shared_venv)`` tuple the old fixture did.
    """
    return shared_launcher


def _bump(venv: Path, project: Path, env: dict[str, str]):
    """Run ``python -m ai_hats._bump_internal`` from the shared venv."""
    return _run(
        [f"{venv}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=project, env=env, timeout=60,
    )


def _seed_pre_hats471_yaml(project: Path) -> None:
    """Materialise an existing v4 project WITHOUT ``migration_step`` — the
    shape of every project upgrading from a release that predates HATS-471.
    """
    project.mkdir(parents=True, exist_ok=True)
    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: gemini\n"
        "ai_hats_dir: .agent/ai-hats\n"
    )


# ----- Test 1: first bump replays registry and persists counter -----


@pytest.mark.integration
def test_e2e_first_bump_replays_registry_and_persists_step(
    installed_launcher, tmp_path,
):
    _launcher, env, venv = installed_launcher
    project = tmp_path / "first_bump"
    _seed_pre_hats471_yaml(project)

    res = _bump(venv, project, env)

    # Banner fired at least once → registry actually advanced.
    assert BANNER_PREFIX in res.stderr, (
        f"expected registry banner on stderr, got:\nSTDOUT:\n{res.stdout}\n"
        f"STDERR:\n{res.stderr}"
    )

    # yaml persisted with the registry's latest step.
    raw = yaml.safe_load((project / "ai-hats.yaml").read_text())
    assert "migration_step" in raw, (
        f"migration_step missing from persisted yaml:\n{raw}"
    )
    # Cross-check the persisted value against the installed package's
    # registry — keeps the test honest if the registry grows.
    latest_step_proc = _run(
        [
            f"{venv}/bin/python", "-c",
            "from ai_hats.migrations import latest_step; print(latest_step())",
        ],
        cwd=project, env=env, timeout=10,
    )
    expected_latest = int(latest_step_proc.stdout.strip())
    assert raw["migration_step"] == expected_latest, (
        f"persisted migration_step={raw['migration_step']}, "
        f"expected latest_step()={expected_latest}"
    )


# ----- Test 2: second bump is gated → no banner -----


@pytest.mark.integration
def test_e2e_second_bump_is_gated_no_banner(installed_launcher, tmp_path):
    """The fail-under-revert assertion: drop the ``if step >= m.step:
    continue`` short-circuit in ``run_pending`` and this test starts
    seeing the banner on the second bump.
    """
    _launcher, env, venv = installed_launcher
    project = tmp_path / "second_bump"
    _seed_pre_hats471_yaml(project)

    # First bump primes the counter to latest (see test 1).
    first = _bump(venv, project, env)
    assert BANNER_PREFIX in first.stderr, "fixture invariant: first bump runs the registry"

    second = _bump(venv, project, env)

    assert BANNER_PREFIX not in second.stderr, (
        "expected NO registry banner on a fully-migrated project, "
        f"got:\nSTDOUT:\n{second.stdout}\nSTDERR:\n{second.stderr}"
    )


# ----- Test 3: greenfield init seeds counter, no registry run -----


@pytest.mark.integration
def test_e2e_greenfield_init_seeds_latest_step(installed_launcher, tmp_path):
    """``ai-hats self init`` on a clean directory writes the yaml with
    ``migration_step = latest`` directly — the registry has nothing to do
    because the directory is fresh."""
    launcher, env, venv = installed_launcher
    project = tmp_path / "greenfield"
    project.mkdir()

    res = _run(
        [str(launcher), "self", "init", "-p", "gemini", "--no-wizard"],
        cwd=project, env=env, timeout=120,
    )

    # No registry banner — init is greenfield, all entries are skipped.
    assert BANNER_PREFIX not in res.stderr, (
        f"unexpected registry banner during greenfield init:\nSTDERR:\n{res.stderr}"
    )

    # yaml carries the latest step from the very first save.
    raw = yaml.safe_load((project / "ai-hats.yaml").read_text())
    latest_step_proc = _run(
        [
            f"{venv}/bin/python", "-c",
            "from ai_hats.migrations import latest_step; print(latest_step())",
        ],
        cwd=project, env=env, timeout=10,
    )
    expected_latest = int(latest_step_proc.stdout.strip())
    assert raw.get("migration_step") == expected_latest, (
        f"greenfield init did not seed migration_step to latest "
        f"(got {raw.get('migration_step')}, expected {expected_latest})"
    )
