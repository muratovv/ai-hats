"""E2E: every known surface has a publish path (HATS-1353).

`self_heal` does not merely advise an install for a selected surface — it runs
`uv pip install <package_name>`, so a surface in `KNOWN_SURFACES` whose
distribution never reaches PyPI turns automatic repair into a hard failure.
That is how `ai-hats-agy` shipped: in the registry, wired into self-heal, absent
from `release-packages.yml`. Reads the registry, not a hardcoded list.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_hats.surfaces_registry import KNOWN_SURFACES

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"

# The integrator ships from release.yml on the v* tag, not from this workflow.
INTEGRATOR_PACKAGE = "ai-hats"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _surface_packages() -> list[str]:
    return sorted(
        info.package_name
        for info in KNOWN_SURFACES.values()
        if info.package_name != INTEGRATOR_PACKAGE
    )


def test_every_surface_package_is_built() -> None:
    """Each surface distribution is built into its own dist dir."""
    build_run = "".join(
        step.get("run", "")
        for step in _workflow()["jobs"]["build"]["steps"]
        if isinstance(step, dict)
    )

    missing = [
        pkg
        for pkg in _surface_packages()
        if f"-o dist-{pkg.removeprefix('ai-hats-')}" not in build_run
    ]

    assert not missing, (
        f"surfaces in KNOWN_SURFACES with no `uv build` step in {WORKFLOW.name}: {missing}. "
        "self_heal runs `uv pip install <package_name>` for these — without a build "
        "step the distribution never reaches PyPI and the repair fails."
    )


def test_every_surface_package_has_a_publish_job() -> None:
    """Each surface has an OIDC publish job in its own distinct environment."""
    jobs = _workflow()["jobs"]

    published: dict[str, dict] = {}
    for job in jobs.values():
        for step in job.get("steps", []):
            packages_dir = (
                (step.get("with") or {}).get("packages-dir") if isinstance(step, dict) else None
            )
            if packages_dir:
                published[packages_dir.rstrip("/")] = job

    missing = []
    for pkg in _surface_packages():
        job = published.get(f"dist-{pkg.removeprefix('ai-hats-')}")
        if job is None:
            missing.append(pkg)
            continue
        assert job.get("environment"), f"{pkg}: publish job has no `environment:`"
        assert job.get("permissions", {}).get("id-token") == "write", (
            f"{pkg}: publish job lacks `id-token: write` — OIDC trusted publishing will fail"
        )

    assert not missing, (
        f"surfaces in KNOWN_SURFACES with no publish job in {WORKFLOW.name}: {missing}"
    )


def test_surface_publish_environments_are_distinct() -> None:
    """PyPI refuses two pending publishers sharing one (repo, workflow, environment)."""
    jobs = _workflow()["jobs"]
    environments = [job["environment"] for job in jobs.values() if job.get("environment")]

    duplicates = {env for env in environments if environments.count(env) > 1}

    assert not duplicates, (
        f"publish jobs share an environment {sorted(duplicates)} — PyPI rejects the "
        "pending trusted publisher as already registered for another project"
    )
