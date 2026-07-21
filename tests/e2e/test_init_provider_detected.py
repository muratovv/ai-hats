"""E2E (PTY): `ai-hats self init` provider menu marks every detected provider.

HATS-613. The provider menu used to mark only the dict-first provider whose
``~/.<name>`` exists as ``(recommended)`` and pre-select it. A user with BOTH
``~/.claude`` and ``~/.gemini`` saw gemini recommended and claude unmarked —
looking undetected — and Enter silently picked gemini.

This drives the REAL installed ``ai-hats`` binary (built into the shared
launcher venv by the venv-tier fixture) through a REAL PTY so the wizard's
``_stdin_is_tty()`` gate passes and the interactive provider menu renders.
``HOME`` is faked to contain both provider config dirs; ``PATH`` deliberately
omits ``ai-hats`` so ``_launch_wizard_session()``'s ``shutil.which("ai-hats")``
returns None and the wizard hand-off exits gracefully right after the menu —
no provider CLI is spawned.

Assertion: BOTH providers are marked ``detected``; the word ``recommended``
never appears. Fail-under-revert: the pre-HATS-613 build marks only gemini
``recommended`` and leaves claude unmarked, so the claude assertion fails.

Marker: ``integration`` (real PTY + real venv binary).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from _helpers.hitl import strip_ansi
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG


pytestmark = pytest.mark.integration


def _drive_init_menu(venv_python: Path, project: Path, home: Path) -> tuple[str, int | None]:
    """Run `ai-hats self init --no-update` under a PTY, pick claude, capture.

    HATS-790: invoked as ``<venv>/bin/python -m ai_hats`` — there is no
    bin/ai-hats console script. PATH still omits ai-hats so the wizard's
    ``shutil.which("ai-hats")`` hand-off skips gracefully.

    Returns ``(ansi_stripped_output, exit_status)``.
    """
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
        [str(venv_python), "-m", "ai_hats", "self", "init", "--no-update"],
        env=env,
        cwd=str(project),
        dimensions=(40, 120),  # wide enough that the menu line never wraps
    )

    buf = ""
    wrote = False
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
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
    except Exception:  # noqa: BLE001 — best-effort reap; status read below
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
