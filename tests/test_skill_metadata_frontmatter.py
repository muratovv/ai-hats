"""HATS-814 — SkillMetadata.from_skill_dir reads hook wiring from SKILL.md
frontmatter top-level ``ai_hats:`` (the engine cutover), with a loud
leftover-sidecar guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.models import LeftoverSidecarHooksError, SkillMetadata
from ai_hats.skill_sidecar import leftover_sidecar_remedy


def _skill(tmp_path: Path, frontmatter: str, *, sidecar: str | None = None) -> Path:
    d = tmp_path / "skills" / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(frontmatter)
    if sidecar is not None:
        (d / "metadata.yaml").write_text(sidecar)
    return d


def test_reads_git_hooks_from_frontmatter(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "description: x\n"
        "ai_hats:\n"
        "  git_hooks:\n"
        "    pre-commit:\n"
        "      - git_hooks/check.sh\n"
        "---\n"
        "# Demo\n",
    )
    md = SkillMetadata.from_skill_dir(d)
    assert md.git_hooks == {"pre-commit": ["git_hooks/check.sh"]}


def test_reads_runtime_hooks_from_frontmatter(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\n"
        "name: demo\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        "      - matcher: Bash\n"
        "        script: hooks/guard.sh\n"
        "---\n"
        "# Demo\n",
    )
    md = SkillMetadata.from_skill_dir(d)
    assert "PreToolUse" in md.runtime_hooks
    row = md.runtime_hooks["PreToolUse"][0]
    assert row.matcher == "Bash"
    assert row.script == "hooks/guard.sh"


def test_hookless_leftover_sidecar_tolerated(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\nname: demo\ndescription: x\n---\n# Demo\n",
        sidecar="name: demo\nauthor: ai-hats\ntags: [x]\n",
    )
    md = SkillMetadata.from_skill_dir(d)  # must not raise
    assert md.git_hooks == {}
    assert md.runtime_hooks == {}


def test_leftover_sidecar_with_git_hooks_raises(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\nname: demo\n---\n# Demo\n",
        sidecar="name: demo\ngit_hooks:\n  pre-commit:\n    - git_hooks/check.sh\n",
    )
    with pytest.raises(LeftoverSidecarHooksError) as exc:
        SkillMetadata.from_skill_dir(d)
    msg = str(exc.value)
    assert "git_hooks" in msg
    assert "ai_hats:" in msg


def test_leftover_sidecar_with_runtime_hooks_raises(tmp_path: Path) -> None:
    d = _skill(
        tmp_path,
        "---\nname: demo\n---\n# Demo\n",
        sidecar=(
            "name: demo\nruntime_hooks:\n  PreToolUse:\n"
            "    - matcher: Bash\n      script: h.sh\n"
        ),
    )
    with pytest.raises(LeftoverSidecarHooksError):
        SkillMetadata.from_skill_dir(d)


def test_guard_message_single_sourced_with_remedy_helper(tmp_path: Path) -> None:
    # R4: the hard-fail guard and the 815 proactive WARN share one remedy
    # string — byte-identical, no drift.
    d = _skill(
        tmp_path,
        "---\nname: demo\n---\n# Demo\n",
        sidecar="name: demo\ngit_hooks:\n  pre-commit:\n    - git_hooks/check.sh\n",
    )
    with pytest.raises(LeftoverSidecarHooksError) as exc:
        SkillMetadata.from_skill_dir(d)
    assert str(exc.value) == leftover_sidecar_remedy("demo", ["git_hooks"])


def test_absent_skill_md_empty_hooks(tmp_path: Path) -> None:
    d = tmp_path / "skills" / "empty"
    d.mkdir(parents=True)
    md = SkillMetadata.from_skill_dir(d)
    assert md.git_hooks == {}
    assert md.runtime_hooks == {}


def test_no_ai_hats_block_empty_hooks(tmp_path: Path) -> None:
    d = _skill(tmp_path, "---\nname: demo\ndescription: x\n---\n# Demo\n")
    md = SkillMetadata.from_skill_dir(d)
    assert md.git_hooks == {}
    assert md.runtime_hooks == {}
