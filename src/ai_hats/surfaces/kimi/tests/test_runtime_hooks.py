"""``plan_hooks`` tests: the per-session KIMI_CODE_HOME the kimi surface plans —
config snapshot + composed [[hooks]], credentials symlink, manifest, env."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.materialize import compose_to_run
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import probe_host
from ai_hats.surfaces import adapt
from ai_hats.surfaces.hook_channel import surface_timeout
from ai_hats.surfaces.kimi import KimiSurface

_HOOKED_SKILL_MD = (
    "---\n"
    "name: guard\n"
    "description: test guard\n"
    "ai_hats:\n"
    "  runtime_hooks:\n"
    "    PreToolUse:\n"
    "      - matcher: Bash\n"
    "        script: hooks/guard.sh\n"
    "---\n"
    "guard\n"
)


def _hooked_library(root: Path) -> Path:
    lib = root / "lib"
    skill_dir = lib / "skills" / "guard"
    (skill_dir / "hooks").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_HOOKED_SKILL_MD)
    script = skill_dir / "hooks" / "guard.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities:\n  - Reliability\n"
        "composition:\n  skills: [guard]\n"
        "injection: '## ROLE\nbody'\n"
    )
    return lib


@pytest.fixture
def hooked_plan(tmp_path, monkeypatch):
    """A composed hooked session plan; the person's kimi home pinned to tmp."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    kimi_home = home / ".kimi-code"
    (kimi_home / "credentials").mkdir(parents=True)
    (kimi_home / "credentials" / "kimi-code-env-x.json").write_text("{}")
    (kimi_home / "config.toml").write_text('default_model = "kimi-code/k3"\n')
    monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_home))
    project = tmp_path / "proj"
    project.mkdir()
    lib = _hooked_library(tmp_path)
    ProjectConfig(provider="kimi", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    result = compose_to_run(asm, "test-role")
    composition = adapt(
        result,
        identity="test-role",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )

    def _plan(policy: SessionPolicy):
        root = asm.layout.cache.session("sid")
        plan = KimiSurface().plan(
            composition,
            run_mode=RunMode.HITL,
            policy=policy,
            root=root,
            layout=asm.layout,
            host=probe_host(),
        )
        return plan, root, kimi_home

    return _plan


def _written(plan) -> dict[Path, object]:
    return {e.target: e for e in plan.entries}


def test_plan_hooks_session_home_config(hooked_plan):
    plan, root, kimi_home = hooked_plan(SessionPolicy(hooks=True, context=True))
    session_home = root / "kimi-home"
    assert plan.env["KIMI_CODE_HOME"] == str(session_home)
    config_entry = _written(plan)[session_home / "config.toml"]
    content = config_entry.content
    # The TOML kimi will actually parse — substrings alone would miss a broken one.
    parsed = tomllib.loads(content)
    assert parsed["default_model"] == "kimi-code/k3"
    hooks = parsed["hooks"]
    assert [h["event"] for h in hooks] == ["PreToolUse", "PostToolUse"]
    assert hooks[0]["command"] == str(root / "hooks" / "PreToolUse")
    # Derived, not pinned: the outer bound must always outlive the chain's own
    # budget, or kimi kills the dispatcher mid-chain and fails open.
    assert hooks[0]["timeout"] == int(surface_timeout())


def test_plan_hooks_credentials_symlink(hooked_plan):
    plan, root, kimi_home = hooked_plan(SessionPolicy(hooks=True, context=True))
    entry = _written(plan)[root / "kimi-home" / "credentials"]
    assert entry.kind.value == "symlink"
    assert Path(entry.source) == kimi_home / "credentials"


def test_plan_hooks_manifest_and_shims(hooked_plan):
    plan, root, _ = hooked_plan(SessionPolicy(hooks=True, context=True))
    written = _written(plan)
    manifest = json.loads(written[root / "hooks.json"].content)
    assert manifest["version"] == 1
    assert manifest["session"]["id"] == "sid"
    assert manifest["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    shim = written[root / "hooks" / "PreToolUse"]
    assert "hook_dispatcher PreToolUse" in shim.content


def test_hooks_policy_off_installs_no_chain(hooked_plan):
    """SessionPolicy(hooks=False) gates session wiring (ADR-0018) — the home
    still stands (AGENTS.md, kimi's own data), but no shims, no manifest, no
    [[hooks]] in the session config."""
    plan, root, _ = hooked_plan(SessionPolicy(hooks=False, context=True))
    written = _written(plan)
    assert root / "hooks.json" not in written
    assert not any(str(t).endswith(("/hooks/PreToolUse", "/hooks/PostToolUse")) for t in written)
    session_home = root / "kimi-home"
    assert plan.env["KIMI_CODE_HOME"] == str(session_home)
    parsed = tomllib.loads(written[session_home / "config.toml"].content)
    assert "hooks" not in parsed
