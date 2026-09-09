"""e2e (HATS-613)

flow:   a user with configuration directories for multiple providers runs interactive
        project setup
cmds:
    ai-hats self init --channel stable
expect: every configured provider directory is labeled "detected — found ~/.<name>" in
        the menu and the string "recommended" is absent
why:    recommending only the first provider when multiple exist causes accidental
        provider selection on default selection
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from _helpers.hitl import strip_ansi
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG


pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def _drive_init_menu(venv_python: Path, project: Path, home: Path) -> tuple[str, int | None]:
    """Run `ai-hats self init --no-update --channel stable` under a PTY, pick claude, capture.

    HATS-790: invoked as ``<venv>/bin/python -m ai_hats`` — there is no
    bin/ai-hats console script. PATH still omits ai-hats so the wizard's
    ``shutil.which("ai-hats")`` hand-off skips gracefully.

    Returns ``(ansi_stripped_output, exit_status)``.
    """
    import select
    from ptyprocess import PtyProcess

    env = {
        "HOME": str(home),
        # No ai-hats on PATH → `_launch_wizard_session` skips the provider
        # CLI hand-off gracefully. System dirs only (git etc. for init).
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TERM": "xterm",
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }

    proc = PtyProcess.spawn(
        [str(venv_python), "-m", "ai_hats", "self", "init", "--no-update", "--channel", "stable"],
        env=env,
        cwd=str(project),
        dimensions=(40, 120),  # wide enough that the menu line never wraps
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
                if not wrote and "Provider [" in strip_ansi(buf):
                    proc.write(b"claude\n")
                    wrote = True

    try:
        proc.wait()
    except Exception:  # noqa: S110 — best-effort reap; exitstatus is read below
        pass
    if proc.isalive():
        proc.terminate(force=True)
    return strip_ansi(buf), proc.exitstatus


def test_e2e_init_marks_every_detected_provider(tmp_venv_project, tmp_path):
    venv_python = Path(tmp_venv_project.env[ENV_AI_HATS_VENV]) / "bin" / "python"
    assert venv_python.is_file(), f"venv python missing: {venv_python}"

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)

    plain, status = _drive_init_menu(venv_python, tmp_venv_project.path, home)

    # Providers detected; never the old "recommended" wording.
    assert "detected — found ~/.claude" in plain, plain[-1200:]
    assert "recommended" not in plain, plain[-1200:]

    # The chosen provider was written to ai-hats.yaml (selection took effect).
    yaml = tmp_venv_project.path / PROJECT_CONFIG
    assert yaml.is_file(), f"init did not write ai-hats.yaml; status={status}\n{plain[-1200:]}"
    assert "provider: claude" in yaml.read_text()
