"""E2E (PTY): `ai-hats self init` launches interactive wizard even on initialized projects and runs offline.

HATS-1215:
1. Verifies that running `ai-hats self init` under a real PTY on an ALREADY INITIALIZED project
   still triggers the interactive wizard prompt.
2. Verifies that `self init` does NOT attempt any network `self update` call.
"""

from __future__ import annotations

import os
import select
import time
from pathlib import Path

import pytest
from ptyprocess import PtyProcess

from _helpers.hitl import strip_ansi
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

pytestmark = pytest.mark.integration


def test_e2e_reinit_launches_wizard_and_runs_offline(tmp_venv_project, tmp_path):
    venv_python = Path(tmp_venv_project.env[ENV_AI_HATS_VENV]) / "bin" / "python"
    assert venv_python.is_file(), f"venv python missing: {venv_python}"

    project = tmp_venv_project.path
    config_file = project / PROJECT_CONFIG

    # Pre-initialize project with a valid ai-hats.yaml
    config_file.write_text(
        "schema_version: 2\n"
        "provider: claude\n"
        "active_role: developer\n"
        "default_role: developer\n"
        "task_prefix: TASK\n"
    )
    assert config_file.exists()

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TERM": "xterm",
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }

    # Run `self init` under PTY on the already-initialized project
    proc = PtyProcess.spawn(
        [str(venv_python), "-m", "ai_hats", "self", "init"],
        env=env,
        cwd=str(project),
        dimensions=(40, 120),
    )

    buf = ""
    wrote = False
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if not proc.isalive():
            break
        r, _, _ = select.select([proc.fd], [], [], 0.5)
        if r:
            try:
                chunk = proc.read(4096)
            except EOFError:
                break
            if chunk:
                buf += chunk.decode(errors="replace")
                plain_text = strip_ansi(buf)
                if not wrote and ("Provider [" in plain_text or "Harness channel" in plain_text):
                    proc.write(b"claude\n")
                    wrote = True

    try:
        proc.wait()
    except Exception:
        pass
    if proc.isalive():
        proc.terminate(force=True)

    plain = strip_ansi(buf)

    # 1. Verify wizard prompt was rendered despite pre-existing config
    assert wrote or "Provider [" in plain or "Harness channel" in plain or "Re-initialized" in plain, (
        f"Wizard prompt was not triggered on re-init. Output:\n{plain}"
    )

    # 2. Verify no self-update network attempt was logged or triggered
    assert "Checking for updates" not in plain
    assert "Updating ai-hats" not in plain
