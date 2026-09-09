"""e2e (HATS-1509)

flow: a developer running self update when settings.json contains tagged broken hook
      references
cmds:
    ai-hats self update
expect: migration step repairs tagged broken hook references restoring valid execution
        paths
why: without tagged ref healing, broken hook references persist in settings.json
     preventing hook execution"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from ai_hats.constants import HOOK_PRE_TOOL_USE
from ai_hats.paths import PROJECT_CONFIG

pytestmark = [pytest.mark.integration, pytest.mark.guards, pytest.mark.install]

# The real-world residue. Materialization never writes this basename today —
# ``managed_runtime_hook_filename`` prefixes every skill-declared script with
# its skill name (``safety-guard-pre_bash_shared_state_guard.sh``).
BROKEN_REL = ".agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh"
RESIDUE_TAG = "ai-hats:hats-437"


def _seed(project_path: Path) -> None:
    (project_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "default_role: assistant\n"
        "active_role: assistant\n"
        "task_prefix: HATS\n"
        # Registry already replayed → the HATS-549 healer won't re-fire and
        # rewrite the entry out from under the sweep.
        "migration_step: 6\n"
    )
    claude = project_path / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    HOOK_PRE_TOOL_USE: [
                        {
                            "matcher": "Bash",
                            "_ai_hats_managed": RESIDUE_TAG,
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"$CLAUDE_PROJECT_DIR/{BROKEN_REL}",
                                }
                            ],
                        }
                    ]
                }
            },
            indent=2,
        )
        + "\n"
    )
    pin_edge_channel(project_path)  # HATS-764: resolve the local source


def _ai_hats_tags(settings: Path) -> list[str]:
    data = json.loads(settings.read_text())
    return [
        str(entry.get("_ai_hats_managed", ""))
        for entries in data.get("hooks", {}).values()
        for entry in entries
        if str(entry.get("_ai_hats_managed", "")).startswith("ai-hats:")
    ]


def test_self_update_sweeps_tagged_ref_before_refusing_on_it(
    tmp_venv_project,
    tmp_path: Path,
) -> None:
    """AC1: tagged + broken → swept, and the run still exits 0.

    The refusal must never win the race: a user whose only symptom is hook
    spam would be left with a bump that fails and no way forward.
    """
    _seed(tmp_venv_project.path)
    settings = tmp_venv_project.path / ".claude" / "settings.json"
    assert not (tmp_venv_project.path / BROKEN_REL).exists(), (
        "fixture precondition: the referenced script must be absent, else "
        "nothing is broken and the test proves nothing"
    )

    res = tmp_venv_project.run(
        "self",
        "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(tmp_path / "backups")},
    )

    assert res.exit_code == 0, (
        f"self update should heal, not refuse:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert _ai_hats_tags(settings) == [], (
        f"residue survived: {_ai_hats_tags(settings)}\nstdout:\n{res.stdout}"
    )
    combined = (res.stdout + res.stderr).replace("\n", " ")
    assert "do not resolve to an existing file" not in combined, (
        f"the end-of-bump refusal fired on an entry the sweep owns:\n{combined}"
    )
