"""e2e (HATS-1874)

flow:   a project whose own `.claude/settings.json` wires the developer's hooks,
        and an ai-hats session built on top of it
cmds:
    apply(ClaudeSurface().plan(...))   # what `ai-hats session` runs
expect: the developer's file is byte-identical afterwards, and the session file
        handed over with `--settings` carries only ai-hats entries
why:    `--settings` is additive — measured on claude 2.1.247 for PreToolUse
        (poc-hook-delivery.md M8), which is the whole reason ai-hats may collapse
        its own entries to one without touching anyone else's. A migration that
        started writing the root file, or swept a user entry out of it, would
        break a contract no unit test watches end to end
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
from pathlib import Path

import pytest

from ai_hats_core import CompositionResult
from ai_hats.paths import claude_dir
from ai_hats.surfaces.claude.provider import ClaudeSurface
from _helpers.sessions import build_session
from tests._plan_helpers import composition_of

pytestmark = [pytest.mark.guards, pytest.mark.surfaces]

SESSION_ID = "sid-user-hooks"
USER_SETTINGS = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "/home/dev/my_own_guard.sh"}],
            }
        ]
    }
}


@pytest.mark.integration
def test_a_session_build_leaves_the_developers_own_hooks_alone(tmp_path: Path) -> None:
    project = tmp_path / "project"
    claude_dir(project).mkdir(parents=True)
    root_settings = claude_dir(project) / "settings.json"
    root_settings.write_text(json.dumps(USER_SETTINGS, indent=2), encoding="utf-8")
    before = root_settings.read_bytes()

    plan = build_session(
        project,
        composition_of(
            CompositionResult(name="r", priorities=[], rules=[], skills=[], injections=[]),
            layout=ProjectLayout.at(project),
        ),
        ClaudeSurface(),
        SESSION_ID,
    )

    assert root_settings.read_bytes() == before, (
        "ai-hats rewrote the developer's own settings — HATS-1874 collapses ITS entries only"
    )
    args = list(plan.launch.args)
    handed_over = args[args.index("--settings") + 1]
    assert Path(handed_over) == plan.root / "settings.json"
    assert "my_own_guard.sh" not in Path(handed_over).read_text(), (
        "the session file absorbed a user entry — the harness merges the two, "
        "so absorbing one means running it twice"
    )
