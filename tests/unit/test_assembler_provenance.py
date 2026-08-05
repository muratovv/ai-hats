"""Unit tests for Assembler component path provenance classification (HATS-525)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_hats.assembler import Assembler
from ai_hats.library_paths import build_library_paths
from ai_hats.models import ComponentType, ProjectConfig, UserConfig
from ai_hats.provenance import ComponentLayer, classify_component_layer


def _make_assembler(tmp_path: Path) -> Assembler:
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".agent" / "ai-hats").mkdir(parents=True, exist_ok=True)
    project_cfg = ProjectConfig(provider="claude", default_role="assistant")
    user_cfg = UserConfig()
    return Assembler(project_dir, project_cfg, user_cfg)


def test_classify_component_layer(tmp_path: Path, monkeypatch) -> None:
    user_home_dir = tmp_path / "user_home"
    user_home_dir.mkdir()
    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    assembler = _make_assembler(tmp_path)

    # 1. Global path
    global_path = user_home_dir / ".ai-hats" / "rules" / "global-rule"
    global_path.mkdir(parents=True)
    assert (
        classify_component_layer(
            global_path,
            project_dir=assembler.project_dir,
            library_paths=assembler.library_paths,
            project_config_paths=assembler.project_config.library_paths,
        )
        == ComponentLayer.GLOBAL
    )
    assert assembler._classify_component_layer(global_path) == ComponentLayer.GLOBAL

    # 2. Project-local path
    proj_lib_path = tmp_path / "project" / "libraries" / "rules" / "proj-rule"
    proj_lib_path.mkdir(parents=True)
    assembler.library_paths.append(tmp_path / "project" / "libraries")
    assert (
        classify_component_layer(
            proj_lib_path,
            project_dir=assembler.project_dir,
            library_paths=assembler.library_paths,
            project_config_paths=assembler.project_config.library_paths,
        )
        == ComponentLayer.PROJECT
    )
    assert assembler._classify_component_layer(proj_lib_path) == ComponentLayer.PROJECT

    # 3. Built-in path (or arbitrary path outside global/project)
    builtin_path = tmp_path / "builtin" / "rules" / "core-rule"
    builtin_path.mkdir(parents=True)
    assert (
        classify_component_layer(
            builtin_path,
            project_dir=assembler.project_dir,
            library_paths=assembler.library_paths,
            project_config_paths=assembler.project_config.library_paths,
        )
        == ComponentLayer.BUILT_IN
    )
    assert assembler._classify_component_layer(builtin_path) == ComponentLayer.BUILT_IN

    # 4. None path
    assert (
        classify_component_layer(
            None,
            project_dir=assembler.project_dir,
            library_paths=assembler.library_paths,
            project_config_paths=assembler.project_config.library_paths,
        )
        == ComponentLayer.BUILT_IN
    )
    assert assembler._classify_component_layer(None) == ComponentLayer.BUILT_IN


def test_get_overlay_provenance_bundled_rule(tmp_path: Path, monkeypatch) -> None:
    user_home_dir = tmp_path / "user_home"
    user_home_dir.mkdir()
    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    assembler = _make_assembler(tmp_path)

    global_rule_dir = user_home_dir / ".ai-hats" / "rules" / "custom-global-rule"
    global_rule_dir.mkdir(parents=True)

    def fake_resolve(name: str, comp_type: ComponentType) -> Path | None:
        if name == "custom-global-rule":
            return global_rule_dir
        return None

    def fake_resolve_rule_dir(name: str) -> Path | None:
        if name == "custom-global-rule":
            return global_rule_dir
        return None

    mock_comp_result = MagicMock()
    mock_comp_result.with_user_rules.return_value = mock_comp_result
    rule_mock = MagicMock()
    rule_mock.name = "custom-global-rule"
    mock_comp_result.rules = [rule_mock]
    mock_comp_result.skills = []

    with (
        patch.object(assembler.resolver, "resolve", side_effect=fake_resolve),
        patch.object(assembler.resolver, "resolve_rule_dir", side_effect=fake_resolve_rule_dir),
        patch.object(assembler.composer, "compose", return_value=mock_comp_result),
    ):
        provenance = assembler._get_overlay_provenance("assistant")
        assert provenance["rules"].get("custom-global-rule") == ComponentLayer.GLOBAL.value


def test_relative_library_path_anchors_to_project_dir(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    rel_lib_dir = project_dir / "custom_libs"
    rel_lib_dir.mkdir()
    rule_dir = rel_lib_dir / "rules" / "rel-rule"
    rule_dir.mkdir(parents=True)

    # Subdirectory outside project_dir to test relative path resolution
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    paths = build_library_paths(project_dir=project_dir, config_paths=["custom_libs"])
    assert rel_lib_dir.resolve() in paths

    layer = classify_component_layer(
        rule_dir.resolve(),
        project_dir=project_dir,
        library_paths=paths,
        project_config_paths=["custom_libs"],
    )
    assert layer == ComponentLayer.PROJECT


def test_classify_global_layer_three_ways(tmp_path: Path, monkeypatch) -> None:
    user_home_dir = tmp_path / "user_home"
    user_home_dir.mkdir()
    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home_dir))

    ai_hats_dir = user_home_dir / ".ai-hats"
    ai_hats_dir.mkdir()

    # Way 1: Regular dir inside ~/.ai-hats
    regular_dir = ai_hats_dir / "rules" / "reg-rule"
    regular_dir.mkdir(parents=True)

    # Way 2: Symlinked child ~/.ai-hats/traits -> real_dir
    real_traits_dir = tmp_path / "external_traits"
    real_traits_dir.mkdir()
    symlinked_rule = real_traits_dir / "sym-rule"
    symlinked_rule.mkdir()
    (ai_hats_dir / "traits").symlink_to(real_traits_dir, target_is_directory=True)
    symlink_path = ai_hats_dir / "traits" / "sym-rule"

    # Way 3: External library configured in ~/.ai-hats/library_paths.yaml
    ext_lib_dir = tmp_path / "external_lib"
    ext_lib_dir.mkdir()
    ext_rule = ext_lib_dir / "rules" / "ext-rule"
    ext_rule.mkdir(parents=True)
    (ai_hats_dir / "library_paths.yaml").write_text(f"paths:\n  - {ext_lib_dir}\n")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".agent" / "ai-hats").mkdir(parents=True)

    lib_paths = build_library_paths(project_dir=project_dir)

    # Way 1 check: regular dir in ~/.ai-hats
    assert (
        classify_component_layer(regular_dir, project_dir=project_dir, library_paths=lib_paths)
        == ComponentLayer.GLOBAL
    )

    # Way 2 check: symlinked child in ~/.ai-hats
    assert (
        classify_component_layer(symlink_path, project_dir=project_dir, library_paths=lib_paths)
        == ComponentLayer.GLOBAL
    )

    # Way 3 check: external library from library_paths.yaml
    assert (
        classify_component_layer(ext_rule, project_dir=project_dir, library_paths=lib_paths)
        == ComponentLayer.GLOBAL
    )
