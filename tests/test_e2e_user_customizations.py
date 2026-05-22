"""E2E test for HATS-421: user-level customizations.yaml end-to-end.

Verifies the full path from CLI write → on-disk file → composer pickup →
final composition contains the global overlay trait.

This is the acceptance-level test from plan.md Step 9 — it sets up a
fixture project + library, writes a global overlay via the CLI, then
asserts that:

1. The trait shows up in the role's effective composition.
2. The traits list returned by ``Assembler.status()`` includes the
   global-overlay trait with provenance="global".
3. Project-level overlay can override the global on the same name
   (project wins).
4. Composer's injection output includes the global trait's injection text.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli.assembly import customize as customize_cmd
from ai_hats.models import ProjectConfig


@pytest.fixture
def fixture_library(tmp_path: Path) -> Path:
    """Tiny library: one role (`worker`) + three traits."""
    lib = tmp_path / "lib"

    def _write_trait(name: str, injection: str):
        d = lib / "traits" / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(
            f"name: {name}\n"
            "composition: {traits: [], rules: [], skills: [], hooks: {}}\n"
            f"injection: '{injection}'\n"
        )

    _write_trait("trait-base", "BASE_TRAIT_TEXT")
    _write_trait("hilt-workflow", "HILT_WORKFLOW_TEXT")
    _write_trait("project-debug", "PROJECT_DEBUG_TEXT")

    (lib / "roles" / "worker").mkdir(parents=True)
    (lib / "roles" / "worker" / "config.yaml").write_text(
        "name: worker\n"
        "priorities: [Reliability]\n"
        "composition:\n"
        "  traits: [trait-base]\n"
        "  rules: []\n"
        "  skills: []\n"
        "  hooks: {}\n"
        "injection: 'WORKER_ROLE_TEXT'\n"
    )
    return lib


@pytest.fixture
def project_dir(monkeypatch, tmp_path: Path) -> Path:
    pdir = tmp_path / "project"
    pdir.mkdir()
    (pdir / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: worker\n"
        "default_role: worker\n"
    )
    monkeypatch.chdir(pdir)
    return pdir


@pytest.fixture
def isolated_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_e2e_global_overlay_picked_up_by_composer(
    isolated_home: Path, project_dir: Path, fixture_library: Path
):
    """CLI --global write → composer compose() returns the global trait."""
    # Write via CLI exactly as a user would.
    runner = CliRunner()
    res = runner.invoke(
        customize_cmd, ["worker", "--add-trait", "hilt-workflow", "--global"]
    )
    assert res.exit_code == 0, res.output
    # File must exist at the user-canonical path.
    user_file = isolated_home / ".ai-hats" / "customizations.yaml"
    assert user_file.exists()

    # Compose via Assembler — global layer must contribute the trait.
    asm = Assembler(project_dir, library_paths=[fixture_library])
    result = asm.composer.compose("worker", overlays=asm._get_overlays("worker"))

    # Composition order in the resolved injections:
    #   trait-base text → hilt-workflow text → role text.
    assert "BASE_TRAIT_TEXT" in result.injections
    assert "HILT_WORKFLOW_TEXT" in result.injections, (
        "global overlay trait not contributed by composer"
    )
    assert "WORKER_ROLE_TEXT" in result.injections


def test_e2e_status_reports_global_provenance(
    isolated_home: Path, project_dir: Path, fixture_library: Path
):
    """Assembler.status()['tree'] tags the trait with layer='global'."""
    runner = CliRunner()
    runner.invoke(customize_cmd, ["worker", "--add-trait", "hilt-workflow", "--global"])

    asm = Assembler(project_dir, library_paths=[fixture_library])
    st = asm.status()
    tree = st["tree"]
    assert "hilt-workflow" in tree["traits"]
    assert tree["provenance"]["traits"]["hilt-workflow"] == "global"
    assert tree["provenance"]["traits"]["trait-base"] == "built-in"


def test_e2e_project_overrides_global(
    isolated_home: Path, project_dir: Path, fixture_library: Path
):
    """Global adds X; project removes X → final composition omits X."""
    runner = CliRunner()
    runner.invoke(customize_cmd, ["worker", "--add-trait", "hilt-workflow", "--global"])
    runner.invoke(customize_cmd, ["worker", "--remove-trait", "hilt-workflow"])  # project

    asm = Assembler(project_dir, library_paths=[fixture_library])
    result = asm.composer.compose("worker", overlays=asm._get_overlays("worker"))
    assert "HILT_WORKFLOW_TEXT" not in result.injections, "project remove should win"


def test_e2e_reset_global_leaves_project_alone(
    isolated_home: Path, project_dir: Path, fixture_library: Path
):
    runner = CliRunner()
    runner.invoke(customize_cmd, ["worker", "--add-trait", "hilt-workflow", "--global"])
    runner.invoke(customize_cmd, ["worker", "--add-trait", "project-debug"])  # project

    # Reset only global — project-debug must survive.
    res = runner.invoke(customize_cmd, ["worker", "--reset", "--global"])
    assert res.exit_code == 0, res.output

    asm = Assembler(project_dir, library_paths=[fixture_library])
    result = asm.composer.compose("worker", overlays=asm._get_overlays("worker"))
    assert "HILT_WORKFLOW_TEXT" not in result.injections
    assert "PROJECT_DEBUG_TEXT" in result.injections


def test_e2e_layered_reorder_via_in_layer_add_remove(
    monkeypatch, tmp_path: Path
):
    """Within a single layer, ``add: X`` + ``remove: X`` moves X to that
    layer's tail — verified by injection order in the composed result.

    Set-up uses a custom library with TWO base traits so the reorder is
    observable (with one base trait the "tail" trivially equals "head").
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    lib = tmp_path / "lib"

    def _write_trait(name: str, text: str):
        d = lib / "traits" / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(
            f"name: {name}\n"
            "composition: {traits: [], rules: [], skills: [], hooks: {}}\n"
            f"injection: '{text}'\n"
        )

    _write_trait("trait-a", "A_TEXT")
    _write_trait("trait-b", "B_TEXT")
    (lib / "roles" / "worker").mkdir(parents=True)
    (lib / "roles" / "worker" / "config.yaml").write_text(
        "name: worker\n"
        "priorities: [Reliability]\n"
        "composition:\n"
        "  traits: [trait-a, trait-b]\n"  # ordered: A then B
        "  rules: []\n"
        "  skills: []\n"
        "  hooks: {}\n"
        "injection: 'ROLE_TEXT'\n"
    )

    pdir = tmp_path / "project"
    pdir.mkdir()
    (pdir / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: worker\n"
        "default_role: worker\n"
    )
    monkeypatch.chdir(pdir)

    # Project overlay: move trait-a to the tail by removing then re-adding.
    pcfg = ProjectConfig.from_yaml(pdir / "ai-hats.yaml")
    from ai_hats.models import OverlayConfig

    pcfg.customizations["worker"] = OverlayConfig(
        remove_traits=["trait-a"],
        add_traits=["trait-a"],
    )
    pcfg.save(pdir / "ai-hats.yaml")

    asm = Assembler(pdir, library_paths=[lib])
    result = asm.composer.compose("worker", overlays=asm._get_overlays("worker"))
    texts = [i for i in result.injections if i in ("A_TEXT", "B_TEXT", "ROLE_TEXT")]
    # Initial composition was [trait-a, trait-b]; reorder moves trait-a to
    # the tail of the traits list, so injection order becomes B → A → ROLE.
    assert texts == ["B_TEXT", "A_TEXT", "ROLE_TEXT"], (
        f"reorder did not produce expected sequence; got {texts}"
    )
