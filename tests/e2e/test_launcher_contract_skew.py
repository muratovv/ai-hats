"""e2e (HATS-1617)

flow: a developer whose host launcher is an older copy than the project's package,
      hitting any ai-hats command on the path where the venv cannot be resolved
cmds:
    ai-hats config status
expect: the failure names the launcher skew and prints the refresh command, instead
        of advising a self init / self update that a stale launcher cannot act on
why: the launcher is a copy that never self-updates, and it dies before any
     interpreter runs — so nothing on the Python side can report the skew. Without
     this the only symptom is a misdirecting hint (HATS-1600 paid a session for it)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats.constants import LAUNCHER_CONTRACT, LAUNCHER_CONTRACT_FILE
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = [pytest.mark.integration, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "ai-hats-launcher"


def _project(tmp_path: Path, *, expects: int | str | None) -> Path:
    """An onboarded project with NO venv, so the launcher takes its failure path.

    ``expects`` is the contract the project's package demands; ``None`` writes no
    marker at all (a project that has not run self update since HATS-1617).
    """
    project = tmp_path / "proj"
    canonical = project / ".agent" / "ai-hats"
    canonical.mkdir(parents=True)
    (project / "ai-hats.yaml").write_text("schema_version: 4\nprovider: claude\n")
    if expects is not None:
        (canonical / LAUNCHER_CONTRACT_FILE).write_text(f"{expects}\n")
    return project


def _run(project: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Drop the session's own pins — they would resolve a real venv and skip the
    # failure path this test is about.
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("AI_HATS_PROJECT_DIR", None)
    env.pop("AI_HATS_DIR", None)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [str(LAUNCHER), "config", "status"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_stale_launcher_names_the_skew(tmp_path: Path) -> None:
    """Project expects a newer contract than this launcher carries."""
    result = _run(_project(tmp_path, expects=LAUNCHER_CONTRACT + 1))

    assert result.returncode == 1, result.stderr
    assert "launcher" in result.stderr.lower()
    assert str(LAUNCHER_CONTRACT + 1) in result.stderr, (
        f"the expected contract is not named:\n{result.stderr}"
    )
    assert "install-launcher.sh" in result.stderr, (
        f"the refresh command is missing:\n{result.stderr}"
    )
    # The advice a stale launcher cannot act on must be gone, not merely appended.
    assert "self update" not in result.stderr, (
        f"still advising a command that cannot fix a stale launcher:\n{result.stderr}"
    )


def test_current_launcher_keeps_the_legacy_hint(tmp_path: Path) -> None:
    """Launcher is level with the project → no skew, today's remediation stands."""
    result = _run(_project(tmp_path, expects=LAUNCHER_CONTRACT))

    assert result.returncode == 1, result.stderr
    assert "self update" in result.stderr
    assert "install-launcher.sh" not in result.stderr


def test_launcher_ahead_of_project_is_not_skew(tmp_path: Path) -> None:
    """One host launcher serves N projects; being newer than an old project is fine."""
    result = _run(_project(tmp_path, expects=0))

    assert "install-launcher.sh" not in result.stderr, (
        f"warned about a launcher that is ahead, not behind:\n{result.stderr}"
    )


@pytest.mark.parametrize("marker", [None, "", "not-a-number"])
def test_indeterminate_marker_stays_quiet(tmp_path: Path, marker: str | None) -> None:
    """Absent or unparseable marker → nothing to compare against; say nothing."""
    result = _run(_project(tmp_path, expects=marker))

    assert "install-launcher.sh" not in result.stderr, (
        f"claimed skew without a readable expectation:\n{result.stderr}"
    )
    assert "self update" in result.stderr
