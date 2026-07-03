"""HATS-907 — e2e: session start auto-heals the marker-proven skills mirror.

Model: ``test_hook_materialization_self_heals.py`` (real composition +
materializers; only the PTY spawn is stubbed). Guarantees, each
fail-under-revert:

1. **Heal**: a planted pre-HATS-294 mirror (marker + listed dir) is swept at
   launch, moved to the safe_delete trash, and announced via a startup NOTE —
   no manual ``self init`` required (the HATS-906 principle).
2. **User data**: a user-authored skill in the same dir, NOT marker-listed,
   survives byte-for-byte.
3. **One-shot** (HATS-469): the second launch is silent for this surface.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main

pytestmark = pytest.mark.integration


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """Real git project + synthetic library whose role ships skill ``alpha``."""
    project = tmp_path / "project"
    project.mkdir()
    _git("init", "--quiet", cwd=project)
    _git("config", "user.email", "t@e.com", cwd=project)
    _git("config", "user.name", "t", cwd=project)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "alpha"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: library skill\n---\n# alpha\n"
    )
    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - alpha\ninjection: B.\n"
    )
    role = lib / "roles" / "mirror-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: mirror-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / "ai-hats.yaml").write_text(
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: mirror-role\n"
        "default_role: mirror-role\n"
        "library_paths:\n  - " + str(lib) + "\n"
    )
    return project, lib


def _launch(project: Path, monkeypatch) -> str:
    from ai_hats import runtime as rt

    monkeypatch.setattr(rt.WrapRunner, "_pty_spawn", lambda self, cmd, env, tracer: 0)
    monkeypatch.setenv("AI_HATS_STARTUP_HOLD", "0.05")
    monkeypatch.chdir(project)
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, (
        f"launch exited {result.exit_code}\n{result.output}\nexc={result.exception!r}"
    )
    return result.output


def test_session_start_heals_stale_skills_mirror(tmp_path: Path, monkeypatch):
    project, lib = _make_project(tmp_path)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("mirror-role", provider_name="claude")

    # Plant the legacy state: marker + listed stale dir + a user-authored
    # neighbour the marker does NOT list.
    mirror = project / ".claude" / "skills"
    (mirror / "alpha").mkdir(parents=True)
    (mirror / "alpha" / "SKILL.md").write_text("# stale pre-294 export\n")
    beta_content = "# my own skill, hands off\n"
    (mirror / "beta").mkdir()
    (mirror / "beta" / "SKILL.md").write_text(beta_content)
    (mirror / ".ai-hats-managed").write_text("alpha\n")

    output = _launch(project, monkeypatch)

    # 1. Healed on disk: stale dir + marker gone ...
    assert not (mirror / "alpha").exists(), "mirror not swept at session start"
    assert not (mirror / ".ai-hats-managed").exists(), "marker not swept"
    # ... recoverable: the discarded copy sits in this process's trash session.
    from ai_hats_core.safe_delete import session_root

    trash = session_root()
    assert trash is not None
    rescued = [
        p for p in trash.rglob("SKILL.md") if p.read_text() == "# stale pre-294 export\n"
    ]
    assert rescued, f"discarded mirror copy not found under {trash}"
    # ... and announced (green NOTE, no CLI verb instructed).
    assert "removed stale ai-hats skills mirror" in output, output
    assert "self init" not in output
    assert "self bump" not in output

    # 2. User-authored neighbour survives byte-for-byte.
    assert (mirror / "beta" / "SKILL.md").read_text() == beta_content

    # 3. One-shot (HATS-469): second launch is silent for this surface.
    second = _launch(project, monkeypatch)
    assert "skills mirror" not in second, second
    assert (mirror / "beta" / "SKILL.md").read_text() == beta_content
