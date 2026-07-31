"""Unit tests for Assembler component path provenance classification (HATS-525)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_hats.assembler import Assembler
from ai_hats.models import ComponentType, ProjectConfig, UserConfig


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
    assert assembler._classify_component_layer(global_path) == "global"

    # 2. Project-local path
    proj_lib_path = tmp_path / "project" / "libraries" / "rules" / "proj-rule"
    proj_lib_path.mkdir(parents=True)
    assembler.library_paths.append(tmp_path / "project" / "libraries")
    assert assembler._classify_component_layer(proj_lib_path) == "project"

    # 3. Built-in path (or arbitrary path outside global/project)
    builtin_path = tmp_path / "builtin" / "rules" / "core-rule"
    builtin_path.mkdir(parents=True)
    assert assembler._classify_component_layer(builtin_path) == "built-in"

    # 4. None path
    assert assembler._classify_component_layer(None) == "built-in"


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
        assert provenance["rules"].get("custom-global-rule") == "global"
