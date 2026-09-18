"""``manifest_rows``: the one writer behind every surface's hook manifest.

A declared hook whose script is not where it should be is never dropped in
silence: the adapter that read the library says so, once, and wires no row.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ai_hats_core.layout import ProjectLayout

from ai_hats.resolver import LibraryResolver
from ai_hats.surfaces import adapt
from ai_hats.surfaces.mirror import manifest_rows

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
    return SimpleNamespace(
        name="r",
        priorities=[],
        rules=[],
        skills=[SimpleNamespace(name=p.name, source_path=p) for p in skills],
        injections=[],
        role_injection="",
        trait_injections={},
        user_rules=[],
        checks=(),
        consent=(),
        errors=(),
    )


def _adapted(tmp_path: Path, *skills: Path):
    diagnostics: list = []
    composition = adapt(
        _result(*skills),
        identity="r",
        layout=ProjectLayout.at(tmp_path / "proj"),
        resolver=LibraryResolver([]),
        overlays=(),
        diagnostics=diagnostics,
    )
    return composition, [d.text for d in diagnostics if "hook" in d.text]


def test_a_healthy_skill_yields_its_row_and_no_notice(tmp_path: Path) -> None:
    source = _hooked_skill(tmp_path)
    skills_dir = tmp_path / "session" / "skills"

    composition, notices = _adapted(tmp_path, source)
    rows = manifest_rows(composition, skills_dir)

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

    composition, notices = _adapted(tmp_path, source)

    assert manifest_rows(composition, tmp_path / "session" / "skills") == {}
    [notice] = notices
    assert "safety-guard" in notice and EVENT in notice and "hooks/guard.sh" in notice
    assert "missing" in notice and "will not run" in notice


def test_a_script_without_the_exec_bit_is_a_notice_naming_it(tmp_path: Path) -> None:
    source = _hooked_skill(tmp_path)
    (source / "hooks" / "guard.sh").chmod(0o644)

    composition, notices = _adapted(tmp_path, source)

    assert manifest_rows(composition, tmp_path / "session" / "skills") == {}
    [notice] = notices
    assert "not executable" in notice and "will not run" in notice


def test_no_composition_declares_nothing(tmp_path: Path) -> None:
    composition, notices = _adapted(tmp_path)
    assert manifest_rows(composition, tmp_path / "skills") == {} and notices == []


def test_the_rows_are_the_same_for_every_projection_of_one_composition(tmp_path: Path) -> None:
    """One derivation, so a dry-run and a launch cannot answer differently."""
    source = _hooked_skill(tmp_path)
    composition, _notices = _adapted(tmp_path, source)

    assert manifest_rows(composition, tmp_path / "a") == manifest_rows(composition, tmp_path / "a")
    assert manifest_rows(composition, tmp_path / "a") != manifest_rows(composition, tmp_path / "b")
