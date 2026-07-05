"""E2E: the host launcher dispatches via ``python -m ai_hats`` (HATS-790, Alt 5).

Value under test: with the ``bin/ai-hats`` console script removed, the bash
launcher (``scripts/ai-hats-launcher``) must run a managed venv via
``<venv>/bin/python -m ai_hats "$@"`` and gate health on a ``python -c
"import ai_hats"`` probe — NOT on the (now non-existent) ``bin/ai-hats`` binary.

Drives the REAL launcher against a managed default ``.venv`` whose ``bin/python``
is a stub that (a) exits 0 for ``-c "import ai_hats"`` (importable) and
(b) echoes a sentinel for ``-m ai_hats <argv>`` — and which deliberately carries
NO ``bin/ai-hats``. The launcher must resolve, pass the import probe, and exec
``python -m ai_hats`` (observable via the sentinel echo).

Fail-under-revert (per ``dev_rule_e2e_gate`` §4): restoring the old final exec
``exec "$VENV/bin/ai-hats" "$@"`` (or the ``[[ ! -x "$VENV/bin/ai-hats" ]]``
fall-through guard) makes the launcher try to exec the absent ``bin/ai-hats`` on
this script-less venv → non-zero exit + "binary is missing" → the assertions
below fail. Real subprocess + real bash launcher; no pip install (cheap).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = pytest.mark.integration

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
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"launcher failed on a script-less venv (HATS-790 regression?):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # The exec went through `python -m ai_hats` (the sentinel proves it), with argv.
    assert f"{SENTINEL}: status --verbose" in result.stdout, (
        f"launcher did not dispatch via `python -m ai_hats`:\n{result.stdout}"
    )
