"""HATS-954 — every workspace package must be wired into release-packages.yml.

observe was silently omitted from the publish workflow (the integrator declared
`ai-hats-observe>=0.2.0` while no publish job existed → a DOA stable-channel
install and a gate-blocked master push). This guards against the next package
being missed the same way: every ``packages/*/pyproject.toml`` ``name`` must be
BOTH built and published by ``release-packages.yml``.

Also owns the environment-uniqueness invariant of that same workflow, which
arrived here from the retired ``test_surface_publish_coverage.py`` (HATS-1826).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"


def _package_names() -> list[str]:
    # One level deep on purpose; tests/test_packages_flat_layout.py keeps that true.
    return [
        tomllib.loads(pp.read_text())["project"]["name"]
        for pp in sorted(REPO_ROOT.glob("packages/*/pyproject.toml"))
    ]


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _publish_dirs(wf: dict) -> set[str]:
    """The ``packages-dir`` of every gh-action-pypi-publish step (slash-stripped)."""
    dirs: set[str] = set()
    for job in wf.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "gh-action-pypi-publish" in step.get("uses", ""):
                dirs.add((step.get("with") or {}).get("packages-dir", "").rstrip("/"))
    return dirs


def _all_run_text(wf: dict) -> str:
    return "\n".join(
        step["run"]
        for job in wf.get("jobs", {}).values()
        for step in job.get("steps", [])
        if step.get("run")
    )


def test_every_package_is_built_and_published() -> None:
    wf = _workflow()
    publish_dirs = _publish_dirs(wf)
    build_text = _all_run_text(wf)

    missing: list[str] = []
    for name in _package_names():
        short = name.removeprefix("ai-hats-")
        built = f"packages/{name}" in build_text
        published = f"dist-{short}" in publish_dirs
        if not (built and published):
            missing.append(f"{name} (built={built}, published={published})")

    assert not missing, (
        "release-packages.yml must build AND publish every workspace package "
        f"(an unwired package ships a DOA stable channel): {missing}"
    )


def test_publish_environments_are_distinct() -> None:
    """PyPI refuses two pending publishers sharing one (repo, workflow, environment).

    HATS-1826: inherited from ``test_surface_publish_coverage.py``, the only
    assertion there that outlived the surface distributions — it reads the
    workflow's publish jobs, never the surface registry.
    """
    jobs = _workflow()["jobs"]
    environments = [job["environment"] for job in jobs.values() if job.get("environment")]

    duplicates = {env for env in environments if environments.count(env) > 1}

    assert not duplicates, (
        f"publish jobs share an environment {sorted(duplicates)} — PyPI rejects the "
        "pending trusted publisher as already registered for another project"
    )
