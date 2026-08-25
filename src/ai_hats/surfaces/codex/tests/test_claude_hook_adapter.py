from __future__ import annotations

from pathlib import Path

from ai_hats.surfaces.codex.claude_hook_adapter import (
    matches_claude_hook,
    to_claude_hook_payloads,
)


def test_codex_tool_names_match_their_claude_hook_aliases() -> None:
    assert matches_claude_hook("Edit|Write|MultiEdit", "apply_patch")
    assert matches_claude_hook("Agent", "spawn_agent")
    assert not matches_claude_hook("Bash", "apply_patch")


def test_permission_request_is_presented_as_claude_pretooluse() -> None:
    (adapted,) = to_claude_hook_payloads(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "shell",
            "tool_input": {"command": "git push"},
        },
        "PermissionRequest",
    )

    assert adapted["hook_event_name"] == "PreToolUse"
    assert adapted["tool_name"] == "shell"


def test_apply_patch_becomes_one_claude_edit_payload_per_target(tmp_path: Path) -> None:
    payloads = to_claude_hook_payloads(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n"
                "*** Update File: src/one.py\n"
                "*** Add File: src/two.py\n"
                "*** End Patch"
            },
            "cwd": str(tmp_path),
        },
        "PreToolUse",
    )

    assert [item["tool_name"] for item in payloads] == ["MultiEdit", "MultiEdit"]
    assert [item["tool_input"]["file_path"] for item in payloads] == [
        str((tmp_path / "src/one.py").resolve()),
        str((tmp_path / "src/two.py").resolve()),
    ]
