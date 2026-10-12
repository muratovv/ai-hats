"""Contract tests for ``KimiSurface``.

Pure-method assertions (no real kimi, no auth): the CLI shape (`-p` last with
its value), home detection and readiness, transcript resolution against the
``$KIMI_CODE_HOME/sessions`` layout, and the planned (ADR-0036) skill mirror
delivered via ``--skills-dir``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats.assembler import Assembler
from ai_hats.materialize import compose_to_run
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import probe_host
from ai_hats.surfaces import adapt
from ai_hats.surfaces.kimi import KimiSurface
from ai_hats.surfaces.kimi.provider import _readiness_findings


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        priorities=["Reliability"],
        merged_injection="## ROLE\nbody",
        rules=[],
        skills=[],
        checks=(),
    )


def test_name_and_detection():
    surface = KimiSurface()
    assert surface.name == "kimi"
    assert surface.detected_home_dirs() == [".kimi-code"]


def test_system_prompt_path_is_project_agents_md():
    layout = SimpleNamespace(root=Path("/proj"))
    assert KimiSurface().system_prompt_path(layout) == Path("/proj/AGENTS.md")


def test_build_system_prompt_composes_sections():
    text = KimiSurface().build_system_prompt(_fake_result())
    assert "## ROLE" in text


def test_cli_command():
    assert KimiSurface().get_cli_command() == ["kimi"]
    assert KimiSurface().get_cli_command(["--plan"]) == ["kimi", "--plan"]


def test_run_command_puts_prompt_last_after_flags():
    cmd = KimiSurface().get_run_command(["kimi", "--skills-dir", "/s"], "PROMPT")
    # kimi misparses `-p --flag`: flags come first, `-p` takes the final value.
    assert cmd == [
        "kimi",
        "--skills-dir",
        "/s",
        "--auto",
        "--output-format=stream-json",
        "-p",
        "PROMPT",
    ]


def test_run_command_strips_interactive_permission_flags():
    cmd = KimiSurface().get_run_command(["kimi", "--yolo"], "PROMPT")
    assert "--yolo" not in cmd
    assert cmd[-2:] == ["-p", "PROMPT"]


def test_readiness_install_when_binary_missing():
    findings = _readiness_findings({"PATH": ""}, which=lambda *a, **k: None)
    assert len(findings) == 1
    assert findings[0].reason == "install"


def test_readiness_reauthenticate_when_no_credentials(tmp_path: Path):
    home = tmp_path / "kimi-home"
    home.mkdir()
    findings = _readiness_findings(
        {"PATH": "/usr/bin"}, which=lambda *a, **k: "/usr/bin/kimi", home=home
    )
    assert len(findings) == 1
    assert findings[0].reason == "reauthenticate"


def test_readiness_clean_when_credentials_and_config_present(tmp_path: Path):
    home = tmp_path / "kimi-home"
    (home / "credentials").mkdir(parents=True)
    (home / "credentials" / "kimi-code-env-x.json").write_text("{}")
    (home / "config.toml").write_text('default_model = "kimi-code/k3"\n')
    findings = _readiness_findings(
        {"PATH": "/usr/bin"}, which=lambda *a, **k: "/usr/bin/kimi", home=home
    )
    assert findings == []


def _wire_session(home: Path, session_id: str) -> Path:
    wire = (
        home / "sessions" / "wd_proj_0123456789ab" / session_id / "agents" / "main" / "wire.jsonl"
    )
    wire.parent.mkdir(parents=True)
    wire.write_text('{"type":"metadata","protocol_version":1}\n')
    return wire


def test_resolve_transcript_exact_by_provider_session_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    wire = _wire_session(tmp_path, "session_abc")
    found = KimiSurface().resolve_transcript(
        Path("/anywhere"), "sid", provider_session_id="session_abc"
    )
    assert found == [wire]


def test_resolve_transcript_unknown_id_falls_back_to_mtime(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    found = KimiSurface().resolve_transcript(
        Path("/anywhere"), "sid", provider_session_id="session_missing"
    )
    assert found == []


def _library(root: Path) -> Path:
    lib = root / "lib"
    skill_dir = lib / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: test\n---\nbody\n")
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities:\n  - Reliability\n"
        "composition:\n  skills: [demo]\n"
        "injection: '## ROLE\nbody'\n"
    )
    return lib


@pytest.fixture
def kimi_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "home" / ".kimi-code"))
    project = tmp_path / "proj"
    project.mkdir()
    lib = _library(tmp_path)
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
    return asm, composition


def test_plan_mirrors_skills_and_passes_skills_dir(kimi_project):
    asm, composition = kimi_project
    surface = KimiSurface()
    plan = surface.plan(
        composition,
        run_mode=RunMode.HITL,
        policy=SessionPolicy(hooks=False, context=True),
        root=asm.layout.cache.session("sid"),
        layout=asm.layout,
        host=probe_host(),
    )
    skills_dir = str(asm.layout.cache.session("sid") / "skills")
    assert ["--skills-dir", skills_dir] == list(plan.launch.args)
    assert plan.surface == "kimi"
