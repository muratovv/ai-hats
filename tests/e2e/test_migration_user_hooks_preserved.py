"""E2E: user-owned hooks survive v4 migration into a dedicated namespace (HATS-549 Phase 4).

Validates the partition + healer-disable behaviour:

  - A user-authored hook script under ``.agent/hooks/`` (basename NOT
    in the ai-hats-owned whitelist) ends up under
    ``<ai_hats_dir>/user-hooks/<name>`` with content + mode preserved.
  - The ``.claude/settings.json`` entry that referenced the legacy
    path is REMOVED (not auto-rewritten). The Stage B inventory file
    under ``<ai_hats_dir>/sessions/audits/`` contains a copy-paste
    re-enable JSON snippet.
  - ai-hats-owned ``.sh`` hooks land under
    ``<ai_hats_dir>/library/hooks/`` and the framework's PreToolUse
    entry resolves correctly (end-of-bump smoke-assert passes).

Per ``dev_rule_e2e_gate``: real ``ai-hats`` binary, real subprocess.
Fail-under-revert against commit ``89e5eab`` (Phase 4).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel


def _seed(project_path: Path) -> None:
    (project_path / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "default_role: assistant\n"
        "active_role: assistant\n"
        "task_prefix: HATS\n"
    )
    hooks = project_path / ".agent" / "hooks"
    hooks.mkdir(parents=True)
    body = "#!/usr/bin/env python3\nprint('user-guard')\n"
    (hooks / "user_guard.py").write_text(body)
    (hooks / "user_guard.py").chmod(0o755)
    # Subdir of user content — partition routes to user-hooks/ too.
    (hooks / "tests").mkdir()
    (hooks / "tests" / "smoke.sh").write_text("#!/bin/sh\nexit 0\n")
    claude = project_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": "$CLAUDE_PROJECT_DIR/.agent/hooks/user_guard.py",
            }],
        }]},
    }, indent=2) + "\n")

    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t.t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(cmd, cwd=str(project_path), check=True)


@pytest.mark.integration
def test_user_owned_hook_relocates_to_user_hooks(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """AC6 part 1: user-authored hook ends up under
    ``<ai_hats_dir>/user-hooks/`` with content + mode preserved."""
    _seed(tmp_venv_project.path)
    pin_edge_channel(tmp_venv_project.path)  # HATS-764: edge so self update resolves the local source
    original_body = (
        tmp_venv_project.path / ".agent" / "hooks" / "user_guard.py"
    ).read_bytes()

    tmp_venv_project.run(
        "self", "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(tmp_path / "backups")},
    )

    user_hook = (
        tmp_venv_project.path / ".agent" / "ai-hats" / "user-hooks"
        / "user_guard.py"
    )
    assert user_hook.is_file(), (
        f"user-owned hook missing from user-hooks/; "
        f".agent/ai-hats/ contents: "
        f"{sorted((tmp_venv_project.path / '.agent' / 'ai-hats').rglob('*'))}"
    )
    assert user_hook.read_bytes() == original_body
    # Execute bit preserved (chmod +x in seed).
    assert user_hook.stat().st_mode & 0o111, (
        f"execute bit lost: mode={oct(user_hook.stat().st_mode)}"
    )
    # Subdir also relocated as a unit.
    assert (
        tmp_venv_project.path / ".agent" / "ai-hats" / "user-hooks"
        / "tests" / "smoke.sh"
    ).is_file()


@pytest.mark.integration
def test_user_owned_hook_not_in_managed_namespace(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """AC6 part 2: managed ``library/hooks/`` must remain ai-hats-only.
    A user .py landing there would be at risk of future framework
    sweeps mistaking it for managed content."""
    _seed(tmp_venv_project.path)
    pin_edge_channel(tmp_venv_project.path)  # HATS-764: edge so self update resolves the local source

    tmp_venv_project.run(
        "self", "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(tmp_path / "backups")},
    )

    managed = (
        tmp_venv_project.path / ".agent" / "ai-hats" / "library" / "hooks"
    )
    # The framework's own .sh hooks live here; the user .py must NOT.
    assert not (managed / "user_guard.py").exists(), (
        f"user-owned hook leaked into managed namespace: "
        f"{sorted(managed.iterdir())}"
    )


@pytest.mark.integration
def test_settings_json_entry_disabled_not_rewritten(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """The hook entry must be REMOVED from settings.json — not
    auto-rewritten to user-hooks/. Phase 4's explicit-disable
    contract: user must re-enable manually after reviewing."""
    _seed(tmp_venv_project.path)
    pin_edge_channel(tmp_venv_project.path)  # HATS-764: edge so self update resolves the local source

    tmp_venv_project.run(
        "self", "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(tmp_path / "backups")},
    )

    raw = (tmp_venv_project.path / ".claude" / "settings.json").read_text()
    # None of these three forms must appear — legacy, post-heal-into-
    # library, OR auto-rewrite-to-user-hooks (the last would silently
    # re-enable, violating the contract).
    assert ".agent/hooks/user_guard.py" not in raw
    assert "library/hooks/user_guard.py" not in raw
    assert "user-hooks/user_guard.py" not in raw, (
        "Phase 4 must DISABLE, not auto-rewrite to user-hooks/"
    )


@pytest.mark.integration
def test_stage_b_inventory_carries_reenable_snippet(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """The Stage B audit-md must include a copy-paste JSON snippet
    pointing at the new ``user-hooks/`` path. That's the entire UX
    payload of explicit-disable: the user needs a one-line snippet
    to put the hook back if they decide they want it."""
    _seed(tmp_venv_project.path)
    pin_edge_channel(tmp_venv_project.path)  # HATS-764: edge so self update resolves the local source

    tmp_venv_project.run(
        "self", "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(tmp_path / "backups")},
    )

    audits = (
        tmp_venv_project.path / ".agent" / "ai-hats" / "sessions" / "audits"
    )
    audit_files = list(audits.glob("*-legacy-refs.md"))
    assert audit_files, (
        f"no Stage B inventory produced; audits dir contents: "
        f"{list(audits.glob('*')) if audits.exists() else 'missing'}"
    )
    body = audit_files[0].read_text()
    assert "user-hook-disabled" in body
    assert "Re-enable snippet" in body
    assert "user-hooks/user_guard.py" in body
    # Markdown structure: fenced JSON block must be present.
    assert "```json" in body
