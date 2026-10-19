"""Contract tests for ``GrokSurface``. No real grok, no auth."""

from __future__ import annotations

import json
import subprocess
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace

from ai_hats.assembler import Assembler
from ai_hats.materialize import compose_to_run
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import probe_host
from ai_hats.surface_registry import get_surface
from ai_hats.surfaces import adapt
from ai_hats.surfaces.grok import GrokSurface
from ai_hats.surfaces.grok.provider import GrokSubagentEngine, _readiness_findings
from ai_hats.surfaces.profiles import ALL


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        priorities=["Reliability"],
        merged_injection="## ROLE\nbody",
        rules=[],
        skills=[],
        checks=(),
    )


def test_name_and_prompt_path():
    surface = GrokSurface()
    assert surface.name == "grok"
    assert surface.system_prompt_path(SimpleNamespace(root=Path("/proj"))) is None
    assert surface.supports_session_command_wrappers() is True
    assert "## ROLE" in surface.build_system_prompt(_fake_result())


def test_get_surface_resolves_the_entry_point():
    assert isinstance(get_surface("grok"), GrokSurface)
    assert any(profile.label == "grok" for profile in ALL)
    assert any(profile.label == "kimi" for profile in ALL)


def test_run_command_puts_the_prompt_last_and_keeps_always_approve():
    cmd = GrokSurface().get_run_command(["grok", "--always-approve"], "PROMPT")
    assert cmd == [
        "grok",
        "--always-approve",
        "--output-format",
        "streaming-messages-json",
        "--always-approve",
        "-p",
        "PROMPT",
    ]


def test_launch_args_add_a_fresh_uuid_and_skip_resume():
    surface = GrokSurface()
    fresh = str(uuid.uuid4())
    assert surface.get_cli_launch_args(["grok"], fresh, False) == ["grok", "--session-id", fresh]
    assert surface.get_cli_launch_args(["grok"], fresh, True) == ["grok"]
    assert surface.get_cli_launch_args(["grok"], "<assigned at launch>", False) == ["grok"]


def test_launch_args_put_session_id_before_the_positional_prompt():
    surface = GrokSurface()
    fresh = str(uuid.uuid4())
    prompt = "первый промпт"
    hitl = surface.get_cli_launch_args(["grok", prompt], fresh, False)
    assert hitl == ["grok", "--session-id", fresh, prompt]
    with_model = surface.get_cli_launch_args(
        ["grok", "--model", "grok-test", prompt], fresh, False
    )
    assert with_model == ["grok", "--session-id", fresh, "--model", "grok-test", prompt]
    assert surface.get_cli_launch_args(["grok", prompt], fresh, True) == ["grok", prompt]
    assert "-p" not in hitl and "--single" not in hitl
    automate = surface.get_run_command(["grok"], "say pong")
    assert automate[automate.index("-p") + 1] == "say pong"


def test_readiness_install_when_binary_missing():
    findings = _readiness_findings({"PATH": ""}, which=lambda *a, **k: None)
    assert [item.reason for item in findings] == ["install"]


def test_readiness_reauthenticate_without_auth_or_key(tmp_path: Path):
    findings = _readiness_findings(
        {"PATH": "/usr/bin"}, which=lambda *a, **k: "/usr/bin/grok", home=tmp_path
    )
    assert [item.reason for item in findings] == ["reauthenticate"]


def _which_grok(*_args, **_kwargs):
    return "/usr/bin/grok"


def test_readiness_accepts_auth_file_or_api_key(tmp_path: Path):
    assert (
        _readiness_findings(
            {"PATH": "/usr/bin", "XAI_API_KEY": "x"}, which=_which_grok, home=tmp_path
        )
        == []
    )
    (tmp_path / "auth.json").write_text("{}")
    assert _readiness_findings({"PATH": "/usr/bin"}, which=_which_grok, home=tmp_path) == []


def test_resolve_transcript_globs_under_the_real_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path))
    session = "sess-grok-pong"
    wire = tmp_path / "sessions" / "%2Fprivate%2Ftmp%2Fwork" / session / "updates.jsonl"
    wire.parent.mkdir(parents=True)
    wire.write_text("{}\n")
    found = GrokSurface().resolve_transcript(Path("/anywhere"), "sid", provider_session_id=session)
    assert found == [wire]


def _library(root: Path) -> Path:
    lib = root / "lib"
    skill_dir = lib / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo\n"
        "description: test\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        "      - matcher: Bash\n"
        "        script: hooks/gate.sh\n"
        "---\n"
        "body\n"
    )
    (skill_dir / "hooks").mkdir()
    gate = skill_dir / "hooks" / "gate.sh"
    gate.write_text("#!/bin/sh\nexit 0\n")
    gate.chmod(0o755)
    role_dir = lib / "roles" / "test-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: test-role\n"
        "priorities:\n  - Reliability\n"
        "composition:\n  skills: [demo]\n"
        "injection: '## ROLE\nbody'\n"
    )
    return lib


def test_plan_writes_the_role_into_the_session_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    real = tmp_path / "home" / ".grok"
    real.mkdir()
    (real / "auth.json").write_text("{}\n")
    (real / "trusted_folders.toml").write_text("\n")
    monkeypatch.delenv("GROK_HOME", raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    lib = _library(tmp_path)
    ProjectConfig(provider="grok", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    composition = adapt(
        compose_to_run(asm, "test-role"),
        identity="test-role",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )
    root = asm.layout.cache.session("sid")
    plan = GrokSurface().plan(
        composition,
        run_mode=RunMode.HITL,
        policy=SessionPolicy(hooks=True, context=True),
        root=root,
        layout=asm.layout,
        host=probe_host(),
    )
    home = root / "grok-home"
    assert plan.context == home / "rules" / "role.md"
    role = next(entry for entry in plan.entries if entry.target == plan.context)
    assert "## ROLE" in role.content
    assert plan.env["GROK_HOME"] == str(home)
    targets = {entry.target for entry in plan.entries}
    assert home / "auth.json" in targets
    assert home / "trusted_folders.toml" in targets
    assert home / "skills" in targets
    assert home / "hooks" / "ai-hats.json" in targets
    assert root / "hooks" / "PreToolUse" in targets
    assert home / "rules" / "role.md" in targets
    config = tomllib.loads(
        next(entry.content for entry in plan.entries if entry.target == home / "config.toml")
    )
    cells = ("skills", "rules", "agents", "mcps", "hooks", "sessions")
    for vendor in ("claude", "cursor", "codex"):
        assert config["compat"][vendor] == dict.fromkeys(cells, False)
    assert "paths" not in config
    expected_ignore = []
    for base in (asm.layout.root, asm.layout.cwd):
        for name in ("skills", "commands"):
            text = str(Path(base).resolve() / ".grok" / name)
            if text not in expected_ignore:
                expected_ignore.append(text)
    assert config["skills"]["ignore"] == expected_ignore
    assert str(home / "skills") not in config["skills"]["ignore"]
    hook_file = next(
        entry for entry in plan.entries if entry.target == home / "hooks" / "ai-hats.json"
    )
    document = json.loads(hook_file.content)
    command = document["hooks"]["PreToolUse"][0]["hooks"][0]
    assert command["command"] == str(root / "hooks" / "PreToolUse")
    assert "matcher" not in document["hooks"]["PreToolUse"][0]
    assert command["timeout"] > 5


def test_session_config_ignores_root_and_cwd_and_roundtrips_quotes(tmp_path: Path):
    from ai_hats.surfaces.grok.runtime_hooks import _session_config

    weird = tmp_path / 'a"b\\c'
    root = str(tmp_path.resolve())
    same = tomllib.loads(_session_config(SimpleNamespace(root=tmp_path, cwd=tmp_path)))
    assert same["skills"]["ignore"] == [f"{root}/.grok/skills", f"{root}/.grok/commands"]
    other = str(weird.resolve())
    both = tomllib.loads(_session_config(SimpleNamespace(root=tmp_path, cwd=weird)))
    assert both["skills"]["ignore"] == [
        f"{root}/.grok/skills",
        f"{root}/.grok/commands",
        f"{other}/.grok/skills",
        f"{other}/.grok/commands",
    ]


def test_automate_plan_keeps_the_role_inline(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("GROK_HOME", raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    lib = _library(tmp_path)
    ProjectConfig(provider="grok", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    composition = adapt(
        compose_to_run(asm, "test-role"),
        identity="test-role",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )
    plan = GrokSurface().plan(
        composition,
        run_mode=RunMode.AUTOMATE,
        policy=SessionPolicy(hooks=False, context=True),
        root=asm.layout.cache.session("sid"),
        layout=asm.layout,
        host=probe_host(),
    )
    assert plan.context is None
    assert plan.launch.args == ()
    home = asm.layout.cache.session("sid") / "grok-home"
    config = tomllib.loads(
        next(entry.content for entry in plan.entries if entry.target == home / "config.toml")
    )
    assert config["compat"]["claude"]["hooks"] is False


class _Metrics:
    def __init__(self) -> None:
        self.values: dict = {}

    def record(self, values) -> None:
        self.values.update(values)


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0, *, hang=False):
        self.pid = 4242
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    def communicate(self, timeout=None):
        if self._hang and not self.killed:
            raise subprocess.TimeoutExpired(cmd="grok", timeout=timeout)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


_STREAM = (
    '{"type":"assistant","message":{"role":"assistant","content":'
    '[{"type":"text","text":"pong"}]},"session_id":"abc"}\n'
    '{"type":"result","subtype":"success","result":"pong","session_id":"abc",'
    '"num_turns":1,"stop_reason":"end_turn"}\n'
)


def test_engine_reads_streaming_messages_and_the_session_id():
    proc = _FakeProc(_STREAM)

    def popen(*args, **kwargs):
        return proc

    metrics = _Metrics()
    result = GrokSubagentEngine(popen=popen).run(
        layout=SimpleNamespace(),
        work_dir=Path("/wd"),
        session_id="sid",
        env={},
        model=None,
        timeout_s=30,
        metrics=metrics,
        launched=SimpleNamespace(args=("grok", "-p", "brief")),
        brief="brief",
        provider_session_id="minted",
    )
    assert result.exit_code == 0
    assert result.stdout == "pong"
    assert metrics.values["provider_session_id"] == "abc"
    assert metrics.values["num_turns"] == 1
    assert metrics.values["stop_reason"] == "end_turn"


def test_engine_reads_a_single_json_object():
    proc = _FakeProc('{"sessionId":"abc","text":"pong"}\n')

    def popen(*args, **kwargs):
        return proc

    metrics = _Metrics()
    result = GrokSubagentEngine(popen=popen).run(
        layout=SimpleNamespace(),
        work_dir=Path("/wd"),
        session_id="sid",
        env={},
        model=None,
        timeout_s=30,
        metrics=metrics,
        launched=SimpleNamespace(args=("grok",)),
        brief=None,
    )
    assert result.stdout == "pong"
    assert metrics.values["grok_session_id"] == "abc"
