"""e2e (HATS-1215)

flow:   a user runs interactive setup in a terminal on a project that already has
        a valid configuration file
cmds:
    ai-hats self init
expect: the interactive Provider menu prompt is displayed despite an existing
        ai-hats.yaml file, and no network check or self-update output appears
why:    re-initialization must allow interactive reconfiguration while honoring offline
        execution guarantees
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

    # Reap only after killing: the wizard keeps prompting past the read
    # deadline, so an unconditional wait() on a live child blocks forever.
    if proc.isalive():
        proc.terminate(force=True)
    try:
        proc.wait()
    except Exception:  # noqa: S110 — best-effort reap of an already-killed child
        pass

    plain = strip_ansi(buf)

    # 1. The wizard PROMPTED, despite a pre-existing ai-hats.yaml.
    # `wrote` is set only after "Surface [" / "Harness channel" appears, so it
    # is the interactive-wizard signal. Do NOT accept "Re-initialized" as proof:
    # init_steps.py prints it on EVERY re-init, wizard or not, which would make
    # this assertion pass even when the wizard never launched (HATS-1215 review).
    assert wrote, f"Wizard prompt was not triggered on re-init. Output:\n{plain}"

    # 2. No self-update was attempted. In this PTY env `PYTEST_CURRENT_TEST` is
    # absent, so a surviving `_run_self_update` call would NOT short-circuit —
    # it would either print "Update skipped" (unmanaged target) or run a real
    # uv install. Absence of all three markers is what makes this offline.
    for marker in ("Checking for updates", "Updating ai-hats", "Update skipped"):
        assert marker not in plain, f"self init attempted an update ({marker!r}):\n{plain}"
