"""Every hook script a library skill declares is executable (HATS-1140, S9).

ADR-0019 D6 rev 7 adds a compose-time exec-bit check for ``checks:`` bindings.
That audit found ``wt_entry_gate.py`` shipped ``100644`` and worked only because
``hooks_manager`` rewrites the mode to ``0o755`` while copying — so the exec bit
was load-bearing and masked. This guard is what would have caught it, and what
keeps it caught as HATS-1268 moves channels to in-place execution.

The assertion is on the git index, not the working tree: mode is what ships.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from ai_hats_wt.carry import parse_worktree_carry

from ai_hats.models import SkillMetadata

LIBRARY_ROOT = Path(__file__).resolve().parents[1] / (
    "packages/ai-hats-library/src/ai_hats_library"
)


def _declared_scripts() -> list[Path]:
    scripts: list[Path] = []
    for skill_dir in sorted(LIBRARY_ROOT.glob("*/skills/*")):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        meta = SkillMetadata.from_skill_dir(skill_dir)
        for declared in (meta.git_hooks or {}).values():
            scripts.extend(skill_dir / script for script in declared)
        for hooks in (meta.runtime_hooks or {}).values():
            scripts.extend(skill_dir / hook.script for hook in hooks)
        # ``worktree`` rides opaque on SkillMetadata (ADR-0014 §2) — parse it
        # through the owning package, as compose time does.
        carry = parse_worktree_carry(meta.worktree, skill_dir.name)
        for row in (*carry.wt_in, *carry.wt_out):
            scripts.append(skill_dir / row.script)
    return scripts


def _index_modes() -> dict[str, str]:
    out = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=LIBRARY_ROOT.parents[3],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    modes = {}
    for line in out.splitlines():
        meta, _, path = line.partition("\t")
        modes[path] = meta.split()[0]
    return modes


def test_library_declares_hook_scripts_at_all():
    """Guard the guard: an empty sweep would make every assertion below vacuous
    (the first attempt at this test parsed frontmatter by hand and found zero)."""
    assert len(_declared_scripts()) >= 10


@pytest.mark.parametrize("script", _declared_scripts(), ids=lambda p: p.name)
def test_declared_hook_script_is_executable_in_the_index(script: Path):
    repo_root = LIBRARY_ROOT.parents[3]
    modes = _index_modes()
    relative = str(script.relative_to(repo_root))

    assert relative in modes, f"{relative} is declared by a skill but not tracked by git"
    assert modes[relative].endswith("755"), (
        f"{relative} is {modes[relative]} — a declared hook script must ship "
        f"executable; copies that chmod on the way out only mask it"
    )
