"""``composed_rows``: the one writer behind every surface's hook manifest (HATS-1862).

A declared hook whose script is not where it should be is never dropped in
silence — the skill's own fault is a notice, ai-hats's fault is an error.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats.hook_collection import RuntimeHookMirrorError, composed_rows
from ai_hats.materialization import ApplyMaterializer, PlanMaterializer

EVENT = "PreToolUse"
MATCHER = "Bash|run_command"


def _hooked_skill(root: Path, name: str = "safety-guard", script: str = "hooks/guard.sh") -> Path:
    source = root / "skill-sources" / name
    (source / "hooks").mkdir(parents=True)
    path = source / script
    path.write_text("#!/bin/sh\nexit 2\n")
    path.chmod(0o700)
    (source / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Guard skill\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        f"    {EVENT}:\n"
        f"      - matcher: {MATCHER}\n"
        f"        script: {script}\n"
        "---\n"
        f"# {name}\n"
    )
    return source


def _result(*skills: Path) -> SimpleNamespace:
    return SimpleNamespace(skills=[SimpleNamespace(name=p.name, source_path=p) for p in skills])


def _mirrored(port, skills_dir: Path, *skills: Path) -> None:
    for source in skills:
        port.copy_tree(source, skills_dir / source.name)


def test_a_healthy_skill_yields_its_row_and_no_notice(tmp_path: Path) -> None:
    source = _hooked_skill(tmp_path)
    skills_dir = tmp_path / "session" / "skills"
    port = ApplyMaterializer()
    _mirrored(port, skills_dir, source)

    rows, notices = composed_rows(_result(source), skills_dir, port=port)

    assert notices == []
    assert rows == {
        EVENT: [
            {
                "matcher": MATCHER,
                "command": str(skills_dir / "safety-guard" / "hooks" / "guard.sh"),
                "tag": f"ai-hats:safety-guard:{EVENT}:{MATCHER}:guard",
            }
        ]
    }


def test_a_script_missing_from_the_skill_is_a_notice_and_no_row(tmp_path: Path) -> None:
    source = _hooked_skill(tmp_path)
    (source / "hooks" / "guard.sh").unlink()  # safe-delete: ok tmp-fixture
    skills_dir = tmp_path / "session" / "skills"
    port = ApplyMaterializer()
    _mirrored(port, skills_dir, source)

    rows, notices = composed_rows(_result(source), skills_dir, port=port)

    assert rows == {}
    [notice] = notices
    assert "safety-guard" in notice and EVENT in notice and "hooks/guard.sh" in notice
    assert "missing" in notice and "will not run" in notice


def test_a_script_without_the_exec_bit_is_a_notice_naming_it(tmp_path: Path) -> None:
    source = _hooked_skill(tmp_path)
    (source / "hooks" / "guard.sh").chmod(0o644)
    skills_dir = tmp_path / "session" / "skills"
    port = ApplyMaterializer()
    _mirrored(port, skills_dir, source)

    rows, notices = composed_rows(_result(source), skills_dir, port=port)

    assert rows == {}
    [notice] = notices
    assert "not executable" in notice and "will not run" in notice


def test_a_script_present_in_the_skill_but_absent_from_the_mirror_refuses(tmp_path: Path) -> None:
    source = _hooked_skill(tmp_path)
    skills_dir = tmp_path / "session" / "skills"
    port = ApplyMaterializer()  # the mirror was never written: ai-hats's fault, not the author's

    with pytest.raises(RuntimeHookMirrorError) as excinfo:
        composed_rows(_result(source), skills_dir, port=port)

    message = str(excinfo.value)
    assert str(skills_dir / "safety-guard" / "hooks" / "guard.sh") in message
    assert "safety-guard" in message and EVENT in message


def test_no_composition_declares_nothing(tmp_path: Path) -> None:
    assert composed_rows(None, tmp_path / "skills", port=ApplyMaterializer()) == ({}, [])


@pytest.mark.parametrize("break_it", [None, "unlink", "chmod"], ids=["healthy", "missing", "no-x"])
def test_a_dry_run_answers_exactly_as_the_real_build(tmp_path: Path, break_it: str | None) -> None:
    source = _hooked_skill(tmp_path)
    if break_it == "unlink":
        (source / "hooks" / "guard.sh").unlink()  # safe-delete: ok tmp-fixture
    elif break_it == "chmod":
        (source / "hooks" / "guard.sh").chmod(0o644)
    real_dir, plan_dir = tmp_path / "real" / "skills", tmp_path / "plan" / "skills"
    real, plan = ApplyMaterializer(), PlanMaterializer()
    _mirrored(real, real_dir, source)
    _mirrored(plan, plan_dir, source)
    assert not plan_dir.exists(), "a dry-run writes no mirror; the port answers from its record"

    real_rows, real_notices = composed_rows(_result(source), real_dir, port=real)
    plan_rows, plan_notices = composed_rows(_result(source), plan_dir, port=plan)

    assert plan_notices == real_notices
    assert [len(v) for v in plan_rows.values()] == [len(v) for v in real_rows.values()]


def test_a_dry_run_refuses_an_unmirrored_script_like_the_real_build(tmp_path: Path) -> None:
    source = _hooked_skill(tmp_path)
    skills_dir = tmp_path / "plan" / "skills"

    with pytest.raises(RuntimeHookMirrorError):
        composed_rows(_result(source), skills_dir, port=PlanMaterializer())
