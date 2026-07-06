"""HATS-931 — e2e: session start heals a *marker-less* pre-marker skills mirror.

Model: ``test_skills_mirror_self_heals.py`` (HATS-907) but the planted mirror has
NO ``.ai-hats-managed`` marker — the real pre-marker export (field report:
~/dotfiles). Fail-under-revert of the ``scope == "project"`` heal partition:
(1) a mirror dir whose name matches a composed skill is swept without a marker
(ownership = the name collision) and announced by a NOTE; (2) a non-composed dir
never collides and survives; (3) the second launch is silent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.paths import PROJECT_CONFIG

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
    (skill / "SKILL.md").write_text("---\nname: alpha\ndescription: library skill\n---\n# alpha\n")
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
    (project / PROJECT_CONFIG).write_text(
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


def test_session_start_heals_markerless_skills_mirror(tmp_path: Path, monkeypatch):
    project, lib = _make_project(tmp_path)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("mirror-role", provider_name="claude")

    # Plant the pre-marker legacy state: a stale mirror dir matching the composed
    # skill ``alpha`` — but NO ``.ai-hats-managed`` marker. ``gamma`` is not a
    # composed skill, so it never collides and must be left untouched.
    mirror = project / ".claude" / "skills"
    (mirror / "alpha").mkdir(parents=True)
    (mirror / "alpha" / "SKILL.md").write_text("# stale pre-marker export\n")
    gamma_content = "# my own skill, not an ai-hats name\n"
    (mirror / "gamma").mkdir()
    (mirror / "gamma" / "SKILL.md").write_text(gamma_content)
    assert not (mirror / ".ai-hats-managed").exists()

    output = _launch(project, monkeypatch)

    # 1. Healed on disk despite the absent marker.
    assert not (mirror / "alpha").exists(), "marker-less mirror not swept at session start"
    # ... recoverable in this process's trash session.
    from ai_hats_core.safe_delete import session_root

    trash = session_root()
    assert trash is not None
    rescued = [p for p in trash.rglob("SKILL.md") if p.read_text() == "# stale pre-marker export\n"]
    assert rescued, f"discarded mirror copy not found under {trash}"
    # ... and announced (green NOTE, no CLI verb instructed).
    assert "removed stale ai-hats skills mirror" in output, output
    assert "self init" not in output
    assert "self bump" not in output

    # 2. Non-composed neighbour never collides → survives byte-for-byte.
    assert (mirror / "gamma" / "SKILL.md").read_text() == gamma_content

    # 3. One-shot: second launch is silent for this surface.
    second = _launch(project, monkeypatch)
    assert "skills mirror" not in second, second
    assert (mirror / "gamma" / "SKILL.md").read_text() == gamma_content
