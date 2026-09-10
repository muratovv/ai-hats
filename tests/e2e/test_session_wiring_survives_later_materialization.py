"""e2e (HATS-1268, HATS-1439)

flow:   a developer initializing a second role in a project while a session is active
cmds:
    ai-hats self init -r hypothesis-intake -p claude --no-update
expect: active session's materialized hook scripts inside session tree remain intact
why:    without per-session cache isolation, initializing a narrower role sweeps active
        session hook scripts
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from _helpers.hook_chain import CLAUDE_PROJECT_DIR_VAR, build_session_settings, pretooluse_hooks

pytestmark = [pytest.mark.integration, pytest.mark.library]


def _resolve_script_path(cmd: str, project_path: Path) -> Path:
    """Resolve the executable script path from a settings.json hook command."""
    # Replace $CLAUDE_PROJECT_DIR/ or $CLAUDE_PROJECT_DIR with actual project path
    if cmd.startswith(CLAUDE_PROJECT_DIR_VAR):
        subbed = cmd.replace(CLAUDE_PROJECT_DIR_VAR, str(project_path) + "/")
    elif "$CLAUDE_PROJECT_DIR" in cmd:
        subbed = cmd.replace("$CLAUDE_PROJECT_DIR", str(project_path))
    else:
        subbed = cmd
    tokens = shlex.split(subbed)
    return Path(tokens[0])


def test_session_wiring_survives_later_materialization(tmp_venv_project) -> None:
    """ADR-0021 M2, M6 | GREEN-pin since HATS-1268."""
    project = tmp_venv_project.path

    # Step 1: Init with maintainer role
    tmp_venv_project.run(
        "self", "init", "-r", "maintainer", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()

    # Step 2: Compose session and materialize its settings.json
    settings = build_session_settings(project, role="maintainer", session_id="sid-1477")
    commands = pretooluse_hooks(settings, "Bash")

    # Precondition assertion: commands list is non-empty and every script path exists
    assert commands, f"precondition failed: no Bash PreToolUse hooks in settings {settings}"

    script_paths = [_resolve_script_path(cmd, project) for cmd in commands]
    missing_before = [p for p in script_paths if not p.exists()]
    assert not missing_before, (
        f"precondition failed: script paths referenced in settings.json do not exist on disk: "
        f"{missing_before}"
    )

    # Step 3: Materialize narrower role (hypothesis-intake) in the same project
    tmp_venv_project.run(
        "self", "init", "-r", "hypothesis-intake", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()

    # Step 4: Verify that all script paths from the existing session wiring still exist
    missing_after = [p for p in script_paths if not p.exists()]
    assert not missing_after, (
        f"later materialization deleted script files referenced by existing session: "
        f"{[str(p) for p in missing_after]}"
    )
