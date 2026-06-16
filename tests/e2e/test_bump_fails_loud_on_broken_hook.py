"""E2E: end-of-bump smoke-assert raises on broken hook refs (HATS-549 Phase 3).

Validates the loud-fail contract:

  - If ``.claude/settings.json`` references a hook command path that
    does NOT resolve to an existing file at the end of bump, the
    process exits non-zero with the assert's diagnosis on stderr.
  - The error carries the Phase 1 backup tarball path and a
    ``tar -xzf`` recovery one-liner — so the user has a single
    command to roll back to pre-bump state.

This is the safety net that catches stuck states inherited from
older ai-hats versions (the proxmox failure mode: silent
"No such file or directory" on every Bash tool call). It also
catches hand-edited settings.json with typos or stale paths.

Per ``dev_rule_e2e_gate``: real ``ai-hats`` binary, real subprocess.
Fail-under-revert against commit ``eaa3294`` (Phase 3).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel


def _seed_stuck_state(project_path: Path) -> None:
    """Mimic proxmox-stuck shape: settings.json post-heal form pointing
    at a hook file that was deleted long ago.

    Bypasses the Phase 4 disable pre-pass (which would catch the
    legacy ``.agent/hooks/`` form) by writing the post-heal path
    directly — exactly what a project caught between an older
    auto-heal and a Phase 4-aware bump looks like.
    """
    (project_path / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "default_role: assistant\n"
        "active_role: assistant\n"
        "task_prefix: HATS\n"
        # migration_step=6 — registry already replayed; healer won't
        # re-fire. The Phase 4 disable pre-pass DOES run on every
        # heal_external_refs call regardless, so to hit the smoke-
        # assert we use a basename ai-hats considers managed
        # (matches the whitelist → not disabled). The script then
        # MUST be missing on disk for the smoke-assert to fire.
        "migration_step: 6\n"
    )
    claude = project_path / ".claude"
    claude.mkdir()
    # NOTE: we point at the framework's own hook name — that bypasses
    # the Phase 4 user-hook disable (which only acts on non-whitelist
    # basenames). The smoke-assert then triggers because the file
    # doesn't exist (the bump's materialization step DOES place it
    # there normally; we'll corrupt that AFTER the bump in the test
    # to simulate post-hoc damage. For this fixture we just write the
    # settings entry and let the test path delete the file).
    (claude / "settings.json").write_text(json.dumps({
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "_ai_hats_managed": "ai-hats:hats-437",
            "hooks": [{
                "type": "command",
                "command": ".agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh",
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
def test_bump_fails_loud_when_settings_points_at_missing_hook(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """AC5: end-of-bump smoke-assert raises AssemblyError that lists
    the broken entry and carries the backup-path recovery one-liner."""
    _seed_stuck_state(tmp_venv_project.path)
    pin_edge_channel(tmp_venv_project.path)  # HATS-764: edge so self update resolves the local source
    backup_dir = tmp_path / "backups"

    # First bump succeeds — it materializes pre_bash_shared_state_guard.sh
    # into .agent/ai-hats/library/hooks/ as part of provider hook setup.
    ok = tmp_venv_project.run(
        "self", "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(backup_dir)},
    )
    assert ok.exit_code == 0, (
        f"first bump (greenfield-ish) should succeed:\n"
        f"stdout:\n{ok.stdout}\nstderr:\n{ok.stderr}"
    )

    # Now corrupt the post-bump state: delete the materialized hook
    # without touching settings.json. The next bump's end-of-bump
    # smoke-assert must catch this. (The smoke-assert runs AFTER
    # _materialize_pretooluse_hooks; if materialization re-creates the
    # file, no failure. To make the failure deterministic we corrupt
    # AFTER materialization by editing settings.json to point at a
    # path that materialization will never write.)
    (tmp_venv_project.path / ".claude" / "settings.json").write_text(
        json.dumps({
            "hooks": {"PreToolUse": [{
                "matcher": "Bash",
                "hooks": [{
                    "type": "command",
                    "command": ".agent/ai-hats/library/hooks/lost_hook.sh",
                }],
            }]},
        }, indent=2) + "\n"
    )

    fail = tmp_venv_project.run(
        "self", "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(backup_dir)},
    )

    assert fail.exit_code != 0, (
        f"smoke-assert should have raised — bump exited 0\n"
        f"stdout:\n{fail.stdout}\nstderr:\n{fail.stderr}"
    )
    # Combined output — CLI renders via Rich which may route to stdout.
    # Strip Rich's soft line wraps so multi-word phrases like
    # "do not resolve to an existing file" match regardless of where
    # the terminal/Rich folder broke the line.
    combined = (fail.stdout + fail.stderr).replace("\n", " ")
    # Diagnosis content — verbatim slice from
    # migration_assert.assert_runtime_hooks_resolve's message.
    assert "hook command path(s) in .claude/settings.json" in combined, (
        f"smoke-assert diagnosis header missing from output:\n"
        f"stdout:\n{fail.stdout}\nstderr:\n{fail.stderr}"
    )
    assert "do not resolve to an existing file" in combined
    # Per-entry line lists the broken command path verbatim.
    assert "lost_hook.sh" in combined, (
        "broken entry's command path must appear in the error"
    )
    # Recovery one-liner with the actual backup path.
    assert "Recovery: tar -xzf" in combined
    backup_match = re.search(
        r"Recovery: tar -xzf (\S+\.tar\.gz)", combined,
    )
    assert backup_match, "recovery one-liner missing backup path"
    backup_path = Path(backup_match.group(1))
    assert backup_path.is_file(), (
        f"recovery-hinted tarball doesn't exist at {backup_path}"
    )
    assert str(backup_dir) in str(backup_path), (
        f"tarball outside isolated backup dir: {backup_path} vs {backup_dir}"
    )


@pytest.mark.integration
def test_bump_passes_smoke_assert_on_clean_state(
    tmp_venv_project, tmp_path: Path,
) -> None:
    """Negative control: a project whose settings.json hook paths
    all resolve must NOT trigger the smoke-assert. This guards the
    happy path against false positives."""
    # Minimal greenfield-ish layout: no .claude/settings.json yet —
    # the bump's provider.ensure_runtime_hooks writes the managed
    # entry pointing at the materialized .sh, which exists.
    (tmp_venv_project.path / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "default_role: assistant\n"
        "active_role: assistant\n"
        "task_prefix: HATS\n"
        "harness:\n  channel: edge\n"  # HATS-764
    )
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t.t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(cmd, cwd=str(tmp_venv_project.path), check=True)

    res = tmp_venv_project.run(
        "self", "update",
        timeout=300,  # HATS-675: 300s = -n8 gate suite norm
        extra_env={"AI_HATS_BUMP_BACKUP_DIR": str(tmp_path / "backups")},
    )

    assert res.exit_code == 0, (
        f"healthy bump should pass smoke-assert:\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    # And the smoke-assert's failure-mode signature should NOT
    # appear in the output of a clean run.
    combined = (res.stdout + res.stderr).replace("\n", " ")
    assert "do not resolve to an existing file" not in combined
