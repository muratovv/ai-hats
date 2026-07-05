"""E2E: ``ai-hats self update`` warns about a leftover hook-bearing
``metadata.yaml`` (HATS-815, detection-only).

A project-local skill under ``libraries/skills/`` still ships a
``metadata.yaml`` carrying ``git_hooks`` after the 814 frontmatter cutover. It
is NOT referenced by the active role, so the 814 compose-guard never reads it —
exactly the gap the proactive bump diagnostic closes. ``self update`` must
SUCCEED (the orphan is not composed) and emit the actionable migrate-by-hand
WARN on stderr, naming the skill.

Per ``dev_rule_e2e_gate``: real ``ai-hats`` binary, real subprocess. The e2e
gate is not strictly triggered (no ``cli/`` change — the detector lives in
``assembler._run_diagnostics``), but the user-facing contract is "warns on
``self update``", so it is proven end-to-end here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from ai_hats.paths import PROJECT_CONFIG


def _seed(project_path: Path) -> None:
    (project_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "default_role: assistant\n"
        "active_role: assistant\n"
        "task_prefix: HATS\n"
    )
    # Project-local orphan skill: valid SKILL.md frontmatter + a leftover
    # hook-bearing metadata.yaml. Not referenced by the assistant role, so it
    # is never composed (the 814 guard never reads it) — but it sits in a
    # resolved library layer, so the bump-time scan finds it.
    orphan = project_path / "libraries" / "skills" / "orphan-hook"
    orphan.mkdir(parents=True)
    (orphan / "SKILL.md").write_text(
        "---\nname: orphan-hook\ndescription: leftover hook skill\n---\n# Orphan\n"
    )
    (orphan / "metadata.yaml").write_text(
        "name: orphan-hook\ngit_hooks:\n  pre-commit:\n    - git_hooks/check.sh\n"
    )

    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t.t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(cmd, cwd=str(project_path), check=True)


@pytest.mark.integration
def test_self_update_warns_on_leftover_hook_sidecar(
    tmp_venv_project, tmp_path: Path,
) -> None:
    _seed(tmp_venv_project.path)
    pin_edge_channel(tmp_venv_project.path)  # edge so self update resolves the local source

    result = tmp_venv_project.run(
        "self", "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(tmp_path / "backups")},
    )

    result.expect_ok()  # orphan is not composed → update succeeds
    assert "orphan-hook" in result.stderr, (
        f"detector did not name the skill; stderr (tail 800):\n"
        f"{result.stderr[-800:]}"
    )
    assert "metadata.yaml still carries" in result.stderr
    assert "ai_hats:" in result.stderr
