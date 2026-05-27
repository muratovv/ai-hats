"""Unit tests for migration_assert (HATS-549 Phase 3).

Covers ``find_broken_hook_refs`` and ``assert_runtime_hooks_resolve``:
shape walk through every Claude Code hook event, path resolution
(absolute / relative / ``$CLAUDE_PROJECT_DIR``), shell-command
skipping, settings.local.json coverage, recovery-hint inclusion.

E2E coverage (real ``ai-hats self update`` subprocess against a
broken-hook fixture) lives in
``tests/e2e/test_bump_fails_loud_on_broken_hook.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.assembler import AssemblyError
from ai_hats.migration_assert import (
    BrokenHookRef,
    SETTINGS_TARGETS,
    assert_runtime_hooks_resolve,
    find_broken_hook_refs,
)


# ---------- Helpers ----------


def _write_settings(
    project_dir: Path,
    settings: dict,
    *,
    local: bool = False,
) -> Path:
    """Write settings.json (or .local.json) under project_dir/.claude/."""
    target = project_dir / ".claude" / (
        "settings.local.json" if local else "settings.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return target


def _seed_hook(project_dir: Path, rel: str) -> Path:
    """Create a real hook script at project_dir/rel."""
    target = project_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o755)
    return target


# ---------- Path resolution ----------


def test_no_settings_no_broken_refs(tmp_path: Path) -> None:
    """A project without any .claude/settings.json is by definition fine."""
    assert find_broken_hook_refs(tmp_path) == []
    assert_runtime_hooks_resolve(tmp_path)  # must not raise


def test_resolves_claude_project_dir_variable(tmp_path: Path) -> None:
    """``$CLAUDE_PROJECT_DIR/`` is expanded to project_dir; the rest of
    the path is joined and stat'd."""
    _seed_hook(tmp_path, ".agent/ai-hats/library/hooks/x.sh")
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/x.sh",
            }],
        }]},
    })

    assert find_broken_hook_refs(tmp_path) == []


def test_resolves_project_relative_path(tmp_path: Path) -> None:
    """A bare relative path is joined onto project_dir (Claude Code's
    behaviour for relative commands)."""
    _seed_hook(tmp_path, ".agent/ai-hats/library/hooks/y.sh")
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": ".agent/ai-hats/library/hooks/y.sh",
            }],
        }]},
    })

    assert find_broken_hook_refs(tmp_path) == []


def test_resolves_absolute_path(tmp_path: Path) -> None:
    hook = _seed_hook(tmp_path, "abs-hook.sh")
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": str(hook)}],
        }]},
    })

    assert find_broken_hook_refs(tmp_path) == []


# ---------- Detection of broken refs ----------


def test_detects_missing_hook_under_claude_project_dir(tmp_path: Path) -> None:
    """The proxmox failure mode: $CLAUDE_PROJECT_DIR/.agent/.../X.py exists
    in settings.json but the file is gone from disk."""
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": "$CLAUDE_PROJECT_DIR/.agent/ai-hats/library/hooks/lost.py",
            }],
        }]},
    })

    broken = find_broken_hook_refs(tmp_path)
    assert len(broken) == 1
    assert broken[0].event == "PreToolUse"
    assert "lost.py" in broken[0].command
    assert not broken[0].resolved_path.exists()


def test_detects_missing_hook_in_settings_local_json(tmp_path: Path) -> None:
    """settings.local.json is scanned too (user-local override file)."""
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": ".agent/missing.sh"}],
        }]},
    }, local=True)

    broken = find_broken_hook_refs(tmp_path)
    assert len(broken) == 1
    assert broken[0].settings_file == ".claude/settings.local.json"


def test_detects_across_multiple_hook_events(tmp_path: Path) -> None:
    """Every Claude Code hook event type is in scope, not just PreToolUse."""
    _write_settings(tmp_path, {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": ".agent/a.sh"}],
            }],
            "SessionEnd": [{
                "matcher": "",
                "hooks": [{"type": "command", "command": ".agent/b.sh"}],
            }],
            "UserPromptSubmit": [{
                "hooks": [{"type": "command", "command": ".agent/c.sh"}],
            }],
        },
    })

    broken = find_broken_hook_refs(tmp_path)
    events = {b.event for b in broken}
    assert events == {"PreToolUse", "SessionEnd", "UserPromptSubmit"}


def test_partial_resolution_returns_only_broken(tmp_path: Path) -> None:
    """A settings file with one valid hook and one broken: only the broken
    one ends up in the result."""
    _seed_hook(tmp_path, ".agent/ai-hats/library/hooks/good.sh")
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{
                    "type": "command",
                    "command": ".agent/ai-hats/library/hooks/good.sh",
                }],
            },
            {
                "matcher": "Bash",
                "hooks": [{
                    "type": "command",
                    "command": ".agent/ai-hats/library/hooks/bad.sh",
                }],
            },
        ]},
    })

    broken = find_broken_hook_refs(tmp_path)
    assert len(broken) == 1
    assert "bad.sh" in broken[0].command


# ---------- Shell-command skip ----------


def test_skips_shell_commands_without_slash(tmp_path: Path) -> None:
    """Pure shell commands (``echo``, ``exit``) don't have slashes and
    aren't file refs. The asserter ignores them."""
    _write_settings(tmp_path, {
        "hooks": {"SessionStart": [{
            "hooks": [
                {"type": "command", "command": "echo hello"},
                {"type": "command", "command": "exit 0"},
                {"type": "command", "command": "true"},
            ],
        }]},
    })

    assert find_broken_hook_refs(tmp_path) == []


def test_skips_non_command_hook_entries(tmp_path: Path) -> None:
    """Hook entries without a ``command`` string are silently skipped.
    Defensive against future Claude Code hook types."""
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [
                {"type": "unknown_type", "config": "..."},
                {"command": 42},  # non-string command
            ],
        }]},
    })

    assert find_broken_hook_refs(tmp_path) == []


# ---------- Malformed JSON ----------


def test_malformed_settings_json_silently_ignored(tmp_path: Path) -> None:
    """Bad JSON is NOT HATS-549's problem to surface — return no findings,
    let JSON-parse errors propagate via other code paths if relevant."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{this is not json")

    assert find_broken_hook_refs(tmp_path) == []
    # And the assert helper must not raise either.
    assert_runtime_hooks_resolve(tmp_path)


def test_missing_hooks_key_silently_ignored(tmp_path: Path) -> None:
    """A settings.json without a top-level ``hooks`` dict is fine."""
    _write_settings(tmp_path, {"otherKey": "value"})
    assert find_broken_hook_refs(tmp_path) == []


# ---------- AssemblyError ----------


def test_assert_raises_on_broken_ref(tmp_path: Path) -> None:
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": "$CLAUDE_PROJECT_DIR/.agent/missing.sh",
            }],
        }]},
    })

    with pytest.raises(AssemblyError) as exc_info:
        assert_runtime_hooks_resolve(tmp_path)
    msg = str(exc_info.value)
    assert "missing.sh" in msg
    assert "PreToolUse" in msg


def test_assert_includes_backup_recovery_hint(tmp_path: Path) -> None:
    """AC5: error message carries the Phase 1 backup path with a
    ``tar -xzf`` one-liner so the user has a recovery handle."""
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": ".agent/missing.sh",
            }],
        }]},
    })
    backup_path = tmp_path / "fake-backup.tar.gz"

    with pytest.raises(AssemblyError) as exc_info:
        assert_runtime_hooks_resolve(tmp_path, backup_path=backup_path)
    msg = str(exc_info.value)
    assert "tar -xzf" in msg
    assert str(backup_path) in msg


def test_assert_skips_recovery_hint_when_no_backup_path(tmp_path: Path) -> None:
    """AI_HATS_BUMP_BACKUP_DIR=- mode: backup_path=None. The diagnosis
    is still emitted; the recovery one-liner is omitted (nothing to
    recover from)."""
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": ".agent/missing.sh",
            }],
        }]},
    })

    with pytest.raises(AssemblyError) as exc_info:
        assert_runtime_hooks_resolve(tmp_path, backup_path=None)
    msg = str(exc_info.value)
    assert "missing.sh" in msg
    assert "tar -xzf" not in msg


def test_assert_lists_every_broken_ref(tmp_path: Path) -> None:
    """A bump that produced multiple broken refs should surface ALL of
    them in one error so the user fixes everything in one pass."""
    _write_settings(tmp_path, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [
                {"type": "command", "command": ".agent/a.sh"},
                {"type": "command", "command": ".agent/b.sh"},
                {"type": "command", "command": ".agent/c.sh"},
            ],
        }]},
    })

    with pytest.raises(AssemblyError) as exc_info:
        assert_runtime_hooks_resolve(tmp_path)
    msg = str(exc_info.value)
    assert "a.sh" in msg
    assert "b.sh" in msg
    assert "c.sh" in msg
    assert "3 hook command" in msg


# ---------- Constants sanity ----------


def test_settings_targets_include_local_overlay() -> None:
    """Regression guard: both settings.json and settings.local.json
    are in the scan scope. settings.local.json is the user-private
    override file and can hold its own hook refs."""
    assert ".claude/settings.json" in SETTINGS_TARGETS
    assert ".claude/settings.local.json" in SETTINGS_TARGETS


def test_broken_hook_ref_is_frozen_dataclass() -> None:
    """BrokenHookRef is immutable so it can be used as a dict key /
    set member if a future caller wants to dedupe."""
    ref = BrokenHookRef(
        settings_file=".claude/settings.json",
        event="PreToolUse",
        command="x",
        resolved_path=Path("/tmp/x"),
    )
    with pytest.raises(Exception):
        ref.command = "y"  # type: ignore[misc]
