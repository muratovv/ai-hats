"""e2e (HATS-1877)

flow:   a maintainer wires a new check into a CI job and forgets its
        dependency, so the stage exits on an import it never had
cmds:
    bash scripts/gates.sh e2e-catalog   # under an interpreter with no click
    bash scripts/gates.sh bidi          # under the real one
expect: a stage that could not import is reported as BROKEN with exit 3, naming
        the missing module and saying nothing above it is a finding; a stage
        that ran and found something keeps exit 1; a green stage is untouched
why:    python exits 1 on an uncaught ModuleNotFoundError exactly as a check
        exits 1 on a finding, so the exit code alone cannot tell them apart.
        The `version-skew-guard` job hosted `e2e-catalog` and `python-pin`
        without their imports and reported each as its own subject for as long
        as neither had ever run — a check that did not run is not a pass
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.repo_src import build_src  # noqa: E402
from _helpers.venv import network_available, venv_unavailable  # noqa: E402

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

STAGE_TIMEOUT_S = 300

#: The exit code `run_py` reserves for "this stage never ran". Distinct from 1
#: (a finding) and from 2 (the dispatcher refusing an unknown stage or a gate).
BROKEN = 3


def _stage(name: str, *, python: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/gates.sh", name],
        cwd=str(cwd),
        env={**os.environ, "PYTHON": python},
        capture_output=True,
        text=True,
        timeout=STAGE_TIMEOUT_S,
    )


@pytest.fixture(scope="module")
def import_less_python(tmp_path_factory: pytest.TempPathFactory) -> str:
    """An interpreter that can start but cannot import what the stage needs."""
    if not network_available():
        venv_unavailable("uv not on PATH — cannot mint a bare interpreter")
    venv = tmp_path_factory.mktemp("bare") / "venv"
    subprocess.run(
        ["uv", "venv", "--python", "3.13", str(venv)],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    return str(venv / "bin" / "python")


def test_a_stage_that_could_not_import_is_named_broken(import_less_python: str):
    """The RED baseline: before HATS-1877 this exited 1 with a bare traceback,
    which is what a stale catalog exits with too."""
    run = _stage("e2e-catalog", python=import_less_python)
    combined = run.stdout + run.stderr

    assert run.returncode == BROKEN, combined
    assert "BROKEN" in combined, combined
    assert "never ran" in combined, combined
    assert "click" in combined, combined
    # The operator must be told not to read the traceback as a verdict.
    assert "Nothing above is a finding" in combined, combined


def test_a_green_stage_passes_through_untouched():
    run = _stage("bidi", python=sys.executable)
    combined = run.stdout + run.stderr

    assert run.returncode == 0, combined
    assert "BROKEN" not in combined, combined


def test_a_finding_is_not_reported_as_a_breakage():
    """Exit 3 on a missing import proves detection, not discrimination: a stage
    that RAN and refused must keep exit 1, or every red check reads as a
    dependency problem."""
    clone = build_src(REPO_ROOT)
    catalog = clone / "tests" / "e2e" / "CATALOG.md"
    if not catalog.exists():
        pytest.skip(f"no CATALOG.md in the clone at {clone}")
    original = catalog.read_text(encoding="utf-8")
    catalog.write_text("# stale on purpose\n", encoding="utf-8")
    try:
        run = _stage("e2e-catalog", python=sys.executable, cwd=clone)
    finally:
        catalog.write_text(original, encoding="utf-8")
    combined = run.stdout + run.stderr

    assert run.returncode == 1, combined
    assert "BROKEN" not in combined, combined
