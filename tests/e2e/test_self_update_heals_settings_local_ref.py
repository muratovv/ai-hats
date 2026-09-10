"""e2e (HATS-1513)

flow:   a developer running self update on a project carrying local settings references
cmds:
    ai-hats self update
expect: migration step heals local settings references to match current project
        structure
why: without local settings healing, invalid paths in settings.json cause hook
     invocation failures"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from ai_hats.constants import HOOK_PRE_TOOL_USE
from ai_hats.paths import PROJECT_CONFIG

pytestmark = [pytest.mark.integration, pytest.mark.guards, pytest.mark.install]

USER_ENTRY = {"matcher": "*", "hooks": [{"type": "command", "command": "echo mine"}]}


def _seed(project_path: Path) -> None:
    (project_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "default_role: assistant\n"
        "active_role: assistant\n"
        "task_prefix: HATS\n"
        "migration_step: 6\n"
    )
    claude = project_path / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / "settings.local.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {
                    HOOK_PRE_TOOL_USE: [
                        {
                            "matcher": "Bash",
                            "_ai_hats_managed": "ai-hats:hats-437",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/"
                                        "pre_bash_shared_state_guard.sh"
                                    ),
                                }
                            ],
                        },
                        USER_ENTRY,
                    ]
                },
            },
            indent=2,
        )
        + "\n"
    )
    pin_edge_channel(project_path)  # HATS-764: resolve the local source


def test_self_update_reclaims_tagged_ref_in_the_private_overlay(
    tmp_venv_project,
    tmp_path: Path,
) -> None:
    _seed(tmp_venv_project.path)
    local = tmp_venv_project.path / ".claude" / "settings.local.json"

    res = tmp_venv_project.run(
        "self",
        "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(tmp_path / "backups")},
    )

    assert res.exit_code == 0, (
        f"the overlay ref must be swept, not refused on:\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    data = json.loads(local.read_text())
    entries = data.get("hooks", {}).get(HOOK_PRE_TOOL_USE, [])
    assert entries == [USER_ENTRY], f"surgical sweep expected, got: {entries}"
    assert data["permissions"] == {"allow": ["Bash(ls:*)"]}, (
        "the user's own settings keys must be untouched"
    )
