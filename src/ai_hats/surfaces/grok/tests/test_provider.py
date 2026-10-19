"""Contract tests for ``GrokSurface``. No real grok, no auth."""

from __future__ import annotations

import json
import logging
import subprocess
import tomllib
import uuid
from datetime import date, datetime, time, timezone
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
    with_model = surface.get_cli_launch_args(["grok", "--model", "grok-test", prompt], fresh, False)
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


def test_plan_writes_the_role_into_the_session_home(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    real = tmp_path / "home" / ".grok"
    real.mkdir()
    (real / "auth.json").write_text("{}\n")
    (real / "trusted_folders.toml").write_text("\n")
    # grok_home() is $GROK_HOME or Path.home(); HOME does not move Path.home().
    monkeypatch.setenv("GROK_HOME", str(real))
    caplog.set_level(logging.WARNING, logger="ai_hats.surfaces.grok.runtime_hooks")
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
    assert set(config) == {"compat", "skills"}
    assert not [
        record for record in caplog.records if record.name == "ai_hats.surfaces.grok.runtime_hooks"
    ]
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


def _hitl_plan(tmp_path: Path):
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
        policy=SessionPolicy(hooks=False, context=False),
        root=root,
        layout=asm.layout,
        host=probe_host(),
    )
    return plan, asm


def _session_document(plan, asm) -> tuple[str, dict]:
    home = asm.layout.cache.session("sid") / "grok-home"
    text = next(entry.content for entry in plan.entries if entry.target == home / "config.toml")
    return text, tomllib.loads(text)


def _plant_grok_home(monkeypatch, home: Path, text: str | None) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GROK_HOME", str(home))
    config = home / "config.toml"
    if text is not None:
        config.write_text(text, encoding="utf-8")
    return config


def _grok_warnings(caplog):
    return [
        record for record in caplog.records if record.name == "ai_hats.surfaces.grok.runtime_hooks"
    ]


def _expected_ignore(layout, prefix: list[str] | None = None) -> list[str]:
    found = list(prefix or [])
    for base in (layout.root, layout.cwd):
        for name in ("skills", "commands"):
            item = str(Path(base).resolve() / ".grok" / name)
            if item not in found:
                found.append(item)
    return found


def _allowlist_document(layout) -> dict:
    from ai_hats.surfaces.grok.runtime_hooks import _session_config

    return tomllib.loads(_session_config(layout))


_PERSON_CONFIG = """\
[ui]
permission_mode = "auto"
max_thoughts_width = 40
yolo = false
opened = 1979-05-27T07:32:00Z
ratio = 1.5
whole = 1.0
birthday = 1979-05-27
alarm = 07:32:00
flags = [1, 2]
mixed = [1, {x = 1}]
"weird key" = "kept"
note = "say \\"hi\\"\\n"

[[marketplace.sources]]
name = "local"

[compat.claude]
skills = true
nickname = "kept"

[skills]
ignore = ["/opt/mine", "/opt/mine"]
custom = "stay"
"""


def test_plan_seeds_person_config_and_forces_session_policy(tmp_path: Path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="ai_hats.surfaces.grok.runtime_hooks")
    project_skill = str((tmp_path / "proj").resolve() / ".grok" / "skills")
    text = _PERSON_CONFIG.replace(
        'ignore = ["/opt/mine", "/opt/mine"]',
        f'ignore = ["/opt/mine", "/opt/mine", "{project_skill}"]',
    )
    _plant_grok_home(monkeypatch, tmp_path / "grok", text)
    plan, asm = _hitl_plan(tmp_path)
    _text, config = _session_document(plan, asm)
    assert config["ui"]["permission_mode"] == "auto"
    assert config["ui"]["max_thoughts_width"] == 40
    assert isinstance(config["ui"]["max_thoughts_width"], int)
    assert config["ui"]["yolo"] is False
    assert config["ui"]["opened"] == datetime(1979, 5, 27, 7, 32, tzinfo=timezone.utc)
    assert config["ui"]["ratio"] == 1.5
    assert isinstance(config["ui"]["whole"], float)
    assert config["ui"]["whole"] == 1.0
    assert config["ui"]["birthday"] == date(1979, 5, 27)
    assert config["ui"]["alarm"] == time(7, 32)
    assert config["ui"]["flags"] == [1, 2]
    assert config["ui"]["mixed"] == [1, {"x": 1}]
    assert config["ui"]["weird key"] == "kept"
    assert config["ui"]["note"] == 'say "hi"\n'
    assert config["marketplace"]["sources"] == [{"name": "local"}]
    assert config["compat"]["claude"]["nickname"] == "kept"
    cells = ("skills", "rules", "agents", "mcps", "hooks", "sessions")
    for vendor in ("claude", "cursor", "codex"):
        for cell in cells:
            assert config["compat"][vendor][cell] is False
    assert config["skills"]["ignore"] == _expected_ignore(asm.layout, ["/opt/mine", project_skill])
    assert config["skills"]["ignore"].count(project_skill) == 1
    assert config["skills"]["custom"] == "stay"
    assert plan.launch.args == ()
    assert not _grok_warnings(caplog)


def test_plan_reads_grok_home_env_not_the_tilde_file(tmp_path: Path, monkeypatch):
    tilde = tmp_path / "tilde"
    tilde.mkdir()
    decoy = tilde / ".grok"
    decoy.mkdir()
    (decoy / "config.toml").write_text(
        '[ui]\npermission_mode = "from-tilde"\n',
        encoding="utf-8",
    )
    # pathlib.Path.home reads passwd, not HOME, so the decoy ~/.grok replaces that call.
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "ai-hats-user"))
    (tmp_path / "ai-hats-user").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tilde))
    _plant_grok_home(
        monkeypatch,
        tmp_path / "env-grok",
        '[ui]\npermission_mode = "from-env"\n',
    )
    plan, asm = _hitl_plan(tmp_path)
    text, config = _session_document(plan, asm)
    assert config["ui"]["permission_mode"] == "from-env"
    assert "from-tilde" not in text


def test_plan_warns_and_uses_the_allowlist_when_the_config_does_not_parse(
    tmp_path: Path, monkeypatch, caplog
):
    caplog.set_level(logging.WARNING, logger="ai_hats.surfaces.grok.runtime_hooks")
    path = _plant_grok_home(monkeypatch, tmp_path / "grok", "[\n")
    plan, asm = _hitl_plan(tmp_path)
    _text, config = _session_document(plan, asm)
    assert config == _allowlist_document(asm.layout)
    warnings = _grok_warnings(caplog)
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    message = warnings[0].getMessage()
    assert str(path) in message
    assert "cannot parse" in message


def test_plan_warns_when_the_person_config_is_unreadable(tmp_path: Path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="ai_hats.surfaces.grok.runtime_hooks")
    path = _plant_grok_home(monkeypatch, tmp_path / "grok", None)
    path.write_bytes(b"\xff")
    plan, asm = _hitl_plan(tmp_path)
    _text, config = _session_document(plan, asm)
    assert config == _allowlist_document(asm.layout)
    warnings = _grok_warnings(caplog)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert str(path) in message
    assert "cannot read" in message


def test_plan_replaces_ignore_that_is_not_a_list_of_strings(tmp_path: Path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="ai_hats.surfaces.grok.runtime_hooks")
    path = _plant_grok_home(
        monkeypatch,
        tmp_path / "grok",
        '[ui]\npermission_mode = "auto"\n\n[skills]\nignore = [1, "/opt/mine"]\ncustom = "stay"\n',
    )
    plan, asm = _hitl_plan(tmp_path)
    _text, config = _session_document(plan, asm)
    assert config["ui"]["permission_mode"] == "auto"
    assert config["skills"]["custom"] == "stay"
    assert config["skills"]["ignore"] == _expected_ignore(asm.layout)
    assert "/opt/mine" not in config["skills"]["ignore"]
    warnings = _grok_warnings(caplog)
    assert len(warnings) == 1
    assert str(path) in warnings[0].getMessage()
    assert "ignore" in warnings[0].getMessage()


def test_plan_adds_session_ignore_when_the_person_omits_it(tmp_path: Path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="ai_hats.surfaces.grok.runtime_hooks")
    _plant_grok_home(
        monkeypatch,
        tmp_path / "grok",
        '[ui]\npermission_mode = "auto"\n\n[skills]\ncustom = "stay"\n',
    )
    plan, asm = _hitl_plan(tmp_path)
    _text, config = _session_document(plan, asm)
    assert config["ui"]["permission_mode"] == "auto"
    assert config["skills"]["custom"] == "stay"
    assert config["skills"]["ignore"] == _expected_ignore(asm.layout)
    assert not _grok_warnings(caplog)


def test_plan_rewrites_compat_and_skills_that_are_not_tables(tmp_path: Path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="ai_hats.surfaces.grok.runtime_hooks")
    _plant_grok_home(
        monkeypatch,
        tmp_path / "grok",
        'compat = "nope"\nskills = "nope"\n\n[ui]\npermission_mode = "auto"\n',
    )
    plan, asm = _hitl_plan(tmp_path)
    _text, config = _session_document(plan, asm)
    assert config["ui"]["permission_mode"] == "auto"
    assert config["compat"]["claude"]["skills"] is False
    assert config["skills"]["ignore"] == _expected_ignore(asm.layout)
    messages = [record.getMessage() for record in _grok_warnings(caplog)]
    assert len(messages) == 2
    assert any("[compat]" in message for message in messages)
    assert any("[skills]" in message for message in messages)


def test_plan_rewrites_a_compat_vendor_that_is_not_a_table(tmp_path: Path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="ai_hats.surfaces.grok.runtime_hooks")
    _plant_grok_home(
        monkeypatch,
        tmp_path / "grok",
        '[ui]\npermission_mode = "auto"\n\n[compat]\nclaude = true\n',
    )
    plan, asm = _hitl_plan(tmp_path)
    _text, config = _session_document(plan, asm)
    assert config["ui"]["permission_mode"] == "auto"
    assert config["compat"]["claude"]["skills"] is False
    warnings = _grok_warnings(caplog)
    assert len(warnings) == 1
    assert "[compat.claude]" in warnings[0].getMessage()


def test_automate_plan_keeps_the_role_inline(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir()
    grok_home = home / ".grok"
    grok_home.mkdir()
    monkeypatch.setenv("GROK_HOME", str(grok_home))
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
