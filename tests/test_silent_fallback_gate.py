"""PreToolUse refuses a new inert except and lets an unrelated edit through."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[1]
    / "packages/ai-hats-library/src/ai_hats_library/usage/skills"
    / "silent-fallback-gate/hooks/silent_fallback_gate.py"
)

_BEFORE = """\
def run():
    try:
        work()
    except Exception:
        return []
"""


def _run(payload: dict, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_a_write_that_adds_an_inert_except_is_denied(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("def work():\n    return 1\n", encoding="utf-8")
    result = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": _BEFORE},
        }
    )
    assert result.returncode == 0, result.stderr
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "# silent-ok:" in reason
    assert "except Exception" in reason


def test_an_edit_that_misses_the_handler_is_allowed(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text(_BEFORE + "\ndef other():\n    return 0\n", encoding="utf-8")
    result = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(target),
                "old_string": "def other():\n    return 0\n",
                "new_string": "def other():\n    return 2\n",
            },
        }
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_a_python_write_with_no_source_is_silent(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    result = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target)},
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert "fail-open" not in result.stderr


def test_an_edit_that_does_not_apply_fails_open(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("x = 1\n", encoding="utf-8")
    result = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(target),
                "old_string": "missing\n",
                "new_string": "present\n",
            },
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert "fail-open" in result.stderr


def test_an_apply_patch_that_adds_an_inert_except_is_denied(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("def work():\n    return 1\n", encoding="utf-8")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: mod.py\n"
        "@@\n"
        " def work():\n"
        "-    return 1\n"
        "+    try:\n"
        "+        return 1\n"
        "+    except Exception:\n"
        "+        return []\n"
        "*** End Patch\n"
    )
    result = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(target), "command": patch},
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "except Exception" in reason


def test_a_cline_patch_that_adds_an_inert_except_is_denied(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    patch = (
        "*** Begin Patch\n"
        "*** Add File: mod.py\n"
        "+def work():\n"
        "+    try:\n"
        "+        return 1\n"
        "+    except Exception:\n"
        "+        return []\n"
        "*** End Patch\n"
    )
    result = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(target), "patch": patch},
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "except Exception" in reason


def test_an_apply_patch_that_misses_the_handler_is_allowed(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text(_BEFORE + "\ndef other():\n    return 0\n", encoding="utf-8")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: mod.py\n"
        "@@\n"
        " def other():\n"
        "-    return 0\n"
        "+    return 2\n"
        "*** End Patch\n"
    )
    result = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(target), "command": patch},
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_an_apply_patch_that_does_not_apply_fails_open(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("x = 1\n", encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: mod.py\n@@\n-missing\n+present\n*** End Patch\n"
    result = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp_path),
            "tool_input": {"file_path": str(target), "command": patch},
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert "fail-open" in result.stderr


def test_a_markdown_write_is_silent(tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    result = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target), "content": "hello\n"},
        }
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""
