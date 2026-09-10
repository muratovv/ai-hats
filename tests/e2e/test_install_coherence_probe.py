"""e2e (HATS-1118)

flow:   a developer running any ai-hats command when a first-party entry point attribute is
        missing
cmds:
    ai-hats config status
expect: launcher probe detects missing entry point attribute and aborts execution with
        exit
        code 1
why: without ep.load() entry-point probes, broken entry point attributes pass find_spec
        checks and crash late during session dispatch"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "ai-hats-launcher"


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.mark.integration
def test_launcher_probe_fails_on_broken_first_party_entry_point(tmp_path: Path) -> None:
    """Launcher probe_imports fails when a first-party provider entry point attribute is missing."""
    venv = tmp_path / ".agent" / "ai-hats" / ".venv"
    bindir = venv / "bin"
    bindir.mkdir(parents=True, exist_ok=True)

    python_stub = bindir / "python"
    python_stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "-c" ]]; then\n'
        '    if [[ "$2" == *"entry_points"* ]]; then\n'
        "        echo \"failed loading entry point 'gemini' (ai_hats.providers:GeminiProvider): module 'ai_hats.providers' has no attribute 'GeminiProvider'\" >&2\n"
        "        exit 1\n"
        "    fi\n"
        "    exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "-m" && "${2:-}" == "ai_hats" ]]; then\n'
        '    echo "SHOULD_NOT_EXEC: $*"\n'
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    _make_executable(python_stub)

    env = os.environ.copy()
    env.pop("AI_HATS_VENV", None)
    env.pop("PYTHONPATH", None)

    proc = subprocess.run(
        [str(LAUNCHER), "status"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"Launcher exited 0 despite broken first-party entry point:\n{out}"
    assert "failed loading entry point" in out, f"Missing expected probe error detail:\n{out}"
    assert "SHOULD_NOT_EXEC" not in out, f"Launcher should have aborted before exec:\n{out}"
