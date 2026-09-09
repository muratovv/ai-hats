"""e2e (HATS-790)

flow: a developer running any ai-hats command on a venv without a bin/ai-hats
      console script
cmds:
    ai-hats config status
expect: launcher verifies python importability and forwards verbatim argv through
        python -m ai_hats
why: without python -m module dispatch, removing console script binaries breaks host
     launcher
        command execution"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = [pytest.mark.integration, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "ai-hats-launcher"

SENTINEL = "module-dispatch-ok"


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _script_less_venv(venv: Path) -> None:
    """A managed venv with bin/python (import-OK + module-dispatch) and NO bin/ai-hats.

    The bin/python stub emulates exactly what the launcher exercises:
      - `python -c "import ai_hats"` → exit 0 (importability probe passes);
      - `python -m ai_hats <argv>`   → echo ``SENTINEL: <argv>`` (the dispatch the
        launcher must take now that there is no console script);
      - anything else                → exit 0.
    Crucially: NO ``bin/ai-hats`` is created, so a reverted launcher that execs it
    fails on this venv.
    """
    bindir = venv / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    python_stub = bindir / "python"
    python_stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "-c" ]]; then exit 0; fi\n'
        'if [[ "${1:-}" == "-m" && "${2:-}" == "ai_hats" ]]; then\n'
        "    shift 2\n"
        f'    echo "{SENTINEL}: $*"\n'
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _make_executable(python_stub)


@pytest.mark.integration
def test_launcher_execs_python_m_on_scriptless_venv(tmp_path: Path) -> None:
    """A managed .venv with bin/python (importable) but NO bin/ai-hats → launcher
    dispatches via `python -m ai_hats` and exits 0."""
    venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    _script_less_venv(venv)
    assert not (venv / "bin" / "ai-hats").exists()  # precondition: no console script

    env = os.environ.copy()
    env.pop(ENV_AI_HATS_VENV, None)  # resolve the default .venv, not an inherited override
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [str(LAUNCHER), "status", "--verbose"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"launcher failed on a script-less venv (HATS-790 regression?):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # The exec went through `python -m ai_hats` (the sentinel proves it), with argv.
    assert f"{SENTINEL}: status --verbose" in result.stdout, (
        f"launcher did not dispatch via `python -m ai_hats`:\n{result.stdout}"
    )
