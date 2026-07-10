"""HATS-632 — the tool-call-hygiene skill declares its PreToolUse Bash guard.

Fail-under-revert wiring proof: remove the ``runtime_hooks`` block from SKILL.md
and ``from_skill_dir`` yields no PreToolUse hook, turning this test red. The
materialization + settings.json wiring itself is covered generically by
``test_assembler_runtime_hooks.py``; here we pin the real skill's declaration.
"""
from pathlib import Path

from ai_hats.models import RuntimeHook, SkillMetadata
from ai_hats.constants import HOOK_PRE_TOOL_USE

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/core/skills/tool-call-hygiene"


def test_declares_pretooluse_bash_guard():
    meta = SkillMetadata.from_skill_dir(SKILL_DIR)
    pre = meta.runtime_hooks.get(HOOK_PRE_TOOL_USE, [])
    assert (
        RuntimeHook(matcher="Bash", script="hooks/tool_call_hygiene_guard.sh") in pre
    ), f"tool-call-hygiene must declare its PreToolUse Bash guard; got {pre!r}"


def test_guard_script_present_and_executable():
    script = SKILL_DIR / "hooks/tool_call_hygiene_guard.sh"
    assert script.is_file()
    assert script.stat().st_mode & 0o111, "guard script must be executable"
