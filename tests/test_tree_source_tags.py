"""Tests for source-tag rendering in ``ai-hats config status`` (HATS-421).

Verifies that the dependency tree exposed by ``Assembler.status()`` carries
a ``provenance`` map and ``traits`` list, and that the values resolve to
``built-in`` / ``global`` / ``project`` per the layered overlay semantics
documented in plan.md.
"""

from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import OverlayConfig, ProjectConfig, UserConfig


@pytest.fixture
def fixture_library(tmp_path: Path) -> Path:
    """Tiny library with one role and three traits — enough to exercise tagging."""
    lib = tmp_path / "lib"
    (lib / "traits" / "trait-base").mkdir(parents=True)
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\n"
        "composition: {traits: [], rules: [], skills: [], hooks: {}}\n"
        "injection: 'base'\n"
    )
    (lib / "traits" / "trait-global-only").mkdir(parents=True)
    (lib / "traits" / "trait-global-only" / "config.yaml").write_text(
        "name: trait-global-only\n"
        "composition: {traits: [], rules: [], skills: [], hooks: {}}\n"
        "injection: 'g'\n"
    )
    (lib / "traits" / "trait-project-only").mkdir(parents=True)
    (lib / "traits" / "trait-project-only" / "config.yaml").write_text(
        "name: trait-project-only\n"
        "composition: {traits: [], rules: [], skills: [], hooks: {}}\n"
        "injection: 'p'\n"
    )
    (lib / "roles" / "demo").mkdir(parents=True)
    (lib / "roles" / "demo" / "config.yaml").write_text(
        "name: demo\n"
        "priorities: [Reliability]\n"
        "composition:\n"
        "  traits: [trait-base]\n"
        "  rules: []\n"
        "  skills: []\n"
        "  hooks: {}\n"
        "injection: 'role injection'\n"
    )
    return lib


@pytest.fixture
def project(monkeypatch, tmp_path: Path):
    pdir = tmp_path / "project"
    pdir.mkdir()
    (pdir / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: demo\n"
        "default_role: demo\n"
    )
    monkeypatch.chdir(pdir)
    return pdir


@pytest.fixture
def isolated_home(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _assembler(project: Path, lib: Path) -> Assembler:
    return Assembler(project, library_paths=[lib])


def test_built_in_only(isolated_home, project, fixture_library):
    asm = _assembler(project, fixture_library)
    st = asm.status()
    tree = st["tree"]
    assert tree["traits"] == ["trait-base"]
    assert tree["provenance"]["traits"].get("trait-base") == "built-in"


def test_global_overlay_tags_added_trait(isolated_home, project, fixture_library):
    # Write a global overlay via UserConfig directly.
    user_path = isolated_home / ".ai-hats" / "customizations.yaml"
    UserConfig(
        customizations={"demo": OverlayConfig(add_traits=["trait-global-only"])}
    ).save(user_path)
    asm = _assembler(project, fixture_library)
    st = asm.status()
    tree = st["tree"]
    assert "trait-global-only" in tree["traits"]
    assert tree["provenance"]["traits"]["trait-global-only"] == "global"
    # trait-base remains built-in
    assert tree["provenance"]["traits"]["trait-base"] == "built-in"


def test_project_overlay_overrides_global_provenance(isolated_home, project, fixture_library):
    # Both layers add the same trait → project wins on provenance label.
    UserConfig(
        customizations={"demo": OverlayConfig(add_traits=["trait-project-only"])}
    ).save(isolated_home / ".ai-hats" / "customizations.yaml")
    # Project layer also adds it (rewrites ai-hats.yaml with customizations)
    pcfg = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    pcfg.customizations["demo"] = OverlayConfig(add_traits=["trait-project-only"])
    pcfg.save(project / "ai-hats.yaml")
    asm = _assembler(project, fixture_library)
    st = asm.status()
    assert st["tree"]["provenance"]["traits"]["trait-project-only"] == "project"


def test_remove_drops_provenance(isolated_home, project, fixture_library):
    # Global removes trait-base — it should disappear from the effective list.
    UserConfig(
        customizations={"demo": OverlayConfig(remove_traits=["trait-base"])}
    ).save(isolated_home / ".ai-hats" / "customizations.yaml")
    asm = _assembler(project, fixture_library)
    st = asm.status()
    assert "trait-base" not in st["tree"]["traits"]
    assert "trait-base" not in st["tree"]["provenance"]["traits"]


def test_project_re_adds_what_global_removed(isolated_home, project, fixture_library):
    """global remove + project add → trait survives, tagged as project."""
    UserConfig(
        customizations={"demo": OverlayConfig(remove_traits=["trait-base"])}
    ).save(isolated_home / ".ai-hats" / "customizations.yaml")
    pcfg = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    pcfg.customizations["demo"] = OverlayConfig(add_traits=["trait-base"])
    pcfg.save(project / "ai-hats.yaml")
    asm = _assembler(project, fixture_library)
    st = asm.status()
    assert "trait-base" in st["tree"]["traits"]
    assert st["tree"]["provenance"]["traits"]["trait-base"] == "project"
