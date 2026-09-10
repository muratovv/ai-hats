"""e2e (HATS-471, HATS-582)

flow:   a developer running self update across framework upgrades
cmds:
    ai-hats self update
expect: first bump replays pending migrations and persists migration_step, while second
        bump
        short-circuits
why: without migration step tracking, every framework update re-executes historic
     migration steps
        on existing projects"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.install


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BANNER_PREFIX = "[ai-hats] running migration"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _bump(venv: Path, project: Path, env: dict[str, str]):
    """Run ``python -m ai_hats._bump_internal`` from the shared venv."""
    return _run(
        [f"{venv}/bin/python", "-m", "ai_hats._bump_internal"],
        cwd=project,
        env=env,
        timeout=60,
    )


def _seed_pre_hats471_yaml(project: Path) -> None:
    """Materialise an existing v4 project WITHOUT ``migration_step`` — the
    shape of every project upgrading from a release that predates HATS-471.
    """
    project.mkdir(parents=True, exist_ok=True)
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )


# ----- Test 1: first bump replays registry and persists counter -----


@pytest.mark.integration
def test_e2e_first_bump_replays_registry_and_persists_step(
    installed_launcher,
    tmp_path,
):
    _launcher, env, venv = installed_launcher
    project = tmp_path / "first_bump"
    _seed_pre_hats471_yaml(project)

    res = _bump(venv, project, env)

    # Banner fired at least once → registry actually advanced.
    assert BANNER_PREFIX in res.stderr, (
        f"expected registry banner on stderr, got:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )

    # yaml persisted with the registry's latest step.
    raw = yaml.safe_load((project / PROJECT_CONFIG).read_text())
    assert "migration_step" in raw, f"migration_step missing from persisted yaml:\n{raw}"
    # Cross-check the persisted value against the installed package's
    # registry — keeps the test honest if the registry grows.
    latest_step_proc = _run(
        [
            f"{venv}/bin/python",
            "-c",
            "from ai_hats.migrations import latest_step; print(latest_step())",
        ],
        cwd=project,
        env=env,
        timeout=10,
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
        [str(launcher), "self", "init", "-p", "claude", "--no-wizard"],
        cwd=project,
        env=env,
        timeout=120,
    )

    # No registry banner — init is greenfield, all entries are skipped.
    assert BANNER_PREFIX not in res.stderr, (
        f"unexpected registry banner during greenfield init:\nSTDERR:\n{res.stderr}"
    )

    # yaml carries the latest step from the very first save.
    raw = yaml.safe_load((project / PROJECT_CONFIG).read_text())
    latest_step_proc = _run(
        [
            f"{venv}/bin/python",
            "-c",
            "from ai_hats.migrations import latest_step; print(latest_step())",
        ],
        cwd=project,
        env=env,
        timeout=10,
    )
    expected_latest = int(latest_step_proc.stdout.strip())
    assert raw.get("migration_step") == expected_latest, (
        f"greenfield init did not seed migration_step to latest "
        f"(got {raw.get('migration_step')}, expected {expected_latest})"
    )
