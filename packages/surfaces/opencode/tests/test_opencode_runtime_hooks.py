"""Runtime-hook materialization tests for the OpenCode surface (HATS-1788)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats.paths import session_cache_dir
from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats_opencode import OpenCodeProvider
from ai_hats_opencode.runtime_hooks import MANIFEST_VERSION, plugin_source


def _make_hooked_skill(tmp_path: Path, name: str = "safety-guard") -> Path:
    source = tmp_path / "skill-sources" / name
    (source / "hooks").mkdir(parents=True)
    script = source / "hooks" / "guard.sh"
    script.write_text("#!/bin/sh\nexit 2\n")
    script.chmod(0o700)
    (source / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Guard skill\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        "      - matcher: Bash|run_command\n"
        "        script: hooks/guard.sh\n"
        "---\n"
        f"# {name}\n"
    )
    return source


def _fake_result(skills: list[Path]) -> SimpleNamespace:
    return SimpleNamespace(
        name="maintainer",
        priorities=[],
        merged_injection="role",
        rules=[],
        user_rules=(),
        skills=[SimpleNamespace(name=path.name, source_path=path) for path in skills],
        checks=(),
    )


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


SESSION_ID = "20260822-000000-1-00000"


def _config(project: Path, provider: OpenCodeProvider) -> dict:
    path = provider.session_config_path(project, SESSION_ID)
    assert path.is_file()
    return json.loads(path.read_text())


def test_hooked_composition_registers_plugin_and_manifest(tmp_path: Path) -> None:
    provider = OpenCodeProvider()
    project = _project(tmp_path)
    result = _fake_result([_make_hooked_skill(tmp_path)])
    artifacts = BuiltArtifacts()

    provider.build_session_artifacts(
        project, result, SESSION_ID, run_mode=RunMode.HITL, artifacts=artifacts
    )

    cache_dir = session_cache_dir(project, SESSION_ID)
    manifest_path = cache_dir / "opencode" / "hooks.json"
    plugin_path = cache_dir / "opencode" / "plugin" / "ai-hats-hooks.mjs"
    assert manifest_path.is_file()
    assert plugin_path.is_file()

    manifest = json.loads(manifest_path.read_text())
    assert manifest["version"] == MANIFEST_VERSION
    assert manifest["session"] == {
        "id": SESSION_ID,
        "ai_hats_dir": str(project / ".agent" / "ai-hats"),
    }
    pre = manifest["hooks"]["PreToolUse"]
    assert pre[0]["matcher"] == "Bash|run_command"
    assert pre[0]["command"].endswith("safety-guard/hooks/guard.sh")
    assert pre[0]["tag"].startswith("ai-hats:safety-guard:PreToolUse:")

    from ai_hats.paths import cache_root

    assert manifest["permissions"] == [
        {
            "permission": "external_directory",
            "prefix": f"{cache_root(project)}/",
            "action": "allow",
        }
    ]

    config = _config(project, provider)
    assert f"file://{plugin_path}" in config["plugin"]

    assert artifacts.extra_env["AI_HATS_SESSION_CACHE_DIR"] == str(cache_dir)


def test_hookless_composition_still_ships_permission_rules(tmp_path: Path) -> None:
    """HATS-1792: the manifest carries role permission policy even without hooks."""
    provider = OpenCodeProvider()
    plain = tmp_path / "skill-sources" / "hatrack"
    plain.mkdir(parents=True)
    (plain / "SKILL.md").write_text("---\nname: hatrack\ndescription: x\n---\nbody\n")

    project = _project(tmp_path)
    provider.build_session_artifacts(
        project,
        _fake_result([plain]),
        SESSION_ID,
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )

    cache_dir = session_cache_dir(project, SESSION_ID)
    manifest = json.loads((cache_dir / "opencode" / "hooks.json").read_text())
    assert manifest["hooks"] == {}
    from ai_hats.paths import cache_root

    assert manifest["permissions"] == [
        {
            "permission": "external_directory",
            "prefix": f"{cache_root(project)}/",
            "action": "allow",
        }
    ]
    config = _config(project, provider)
    assert config.get("plugin"), "permission dispatcher must stay registered"


def test_unresolvable_script_is_skipped_from_manifest(tmp_path: Path) -> None:
    source = _make_hooked_skill(tmp_path)
    (source / "hooks" / "guard.sh").unlink()

    provider = OpenCodeProvider()
    project = _project(tmp_path)
    artifacts = BuiltArtifacts()
    provider.build_session_artifacts(
        project,
        _fake_result([source]),
        SESSION_ID,
        run_mode=RunMode.HITL,
        artifacts=artifacts,
    )

    cache_dir = session_cache_dir(project, SESSION_ID)
    manifest = json.loads((cache_dir / "opencode" / "hooks.json").read_text())
    assert manifest["hooks"]["PreToolUse"] == []
    config = _config(project, provider)
    assert "plugin" in config, "dispatcher stays registered; empty lists dispatch nothing"


def test_plugin_asset_is_fail_open_on_missing_pin_and_maps_tools() -> None:
    source = plugin_source()

    assert "process.env.AI_HATS_SESSION_CACHE_DIR" in source
    assert "hookless composition" in source, "missing pin must be an inert no-op, not an error"
    assert "version !== MANIFEST_VERSION" in source


def test_plugin_asset_answers_permission_asks_through_server_api() -> None:
    """HATS-1792: decisions ride the bus event + server reply, rules may defer."""
    source = plugin_source()

    assert '"permission.asked"' in source
    assert "postSessionIdPermissionsPermissionId" in source
    assert 'action === "allow" ? "once" : "reject"' in source
    assert "if (!rule) return;" in source, "unruled asks defer to the platform channel"
