"""Contract tests for the Codex CLI surface (HATS-1531)."""

from __future__ import annotations

import tomllib
from subprocess import CompletedProcess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats.paths import session_cache_dir
from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats_codex import CodexProvider
from ai_hats_codex.provider import _readiness_warnings


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    codex_home = tmp_path / "user-codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)
    monkeypatch.delenv("AI_HATS_CODEX_BASE_HOME", raising=False)


def _make_skill(tmp_path: Path, name: str, description: str = "Test skill") -> Path:
    source = tmp_path / "skill-sources" / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\nInstructions.\n"
    )
    return source


def _make_rule(tmp_path: Path, name: str = "safe-edits") -> SimpleNamespace:
    source = tmp_path / "rule-sources" / name
    source.mkdir(parents=True)
    (source / "rule.md").write_text("Never bypass repository safety checks.\n")
    return SimpleNamespace(name=name, source_path=source)


def _fake_result(
    *, skills: list[Path] | None = None, rules: list[SimpleNamespace] | None = None
) -> SimpleNamespace:
    skill_objs = [SimpleNamespace(name=path.name, source_path=path) for path in skills or []]
    return SimpleNamespace(
        priorities=["Reliability"],
        merged_injection="## ROLE\nYou are the repository maintainer.",
        rules=rules or [],
        user_rules=(),
        skills=skill_objs,
        checks=(),
    )


def _developer_instructions(args: list[str]) -> str:
    index = args.index("-c")
    parsed = tomllib.loads(args[index + 1])
    return parsed["developer_instructions"]


def test_provider_identity_and_inline_only_contract(tmp_path: Path) -> None:
    provider = CodexProvider()
    assert provider.name == "codex"
    assert provider.detected_home_dirs() == [".codex"]
    assert provider.supports_session_command_wrappers() is True
    assert provider.system_prompt_path(tmp_path) is None
    assert provider.rules_dir(tmp_path / "session") == tmp_path / "session" / "rules"


def test_get_cli_command_preserves_safe_passthrough() -> None:
    provider = CodexProvider()
    assert provider.get_cli_command() == ["codex"]
    assert provider.get_cli_command(["--profile", "work"]) == ["codex", "--profile", "work"]


def test_readiness_reports_missing_cli_without_running_a_command() -> None:
    warnings = _readiness_warnings(
        which=lambda name: None,
        run=lambda *args, **kwargs: pytest.fail("missing CLI must not be executed"),
    )

    assert "not on PATH" in warnings[0]


def test_readiness_reports_logged_out_without_exposing_command_output() -> None:
    results = iter(
        [
            CompletedProcess(["codex", "--version"], 0, "codex-cli 0.147.0", ""),
            CompletedProcess(["codex", "login", "status"], 1, "private auth detail", ""),
        ]
    )
    warnings = _readiness_warnings(
        which=lambda name: "/bin/codex",
        run=lambda *args, **kwargs: next(results),
    )

    assert warnings == ["Codex is not authenticated. Run `codex login`, then retry ai-hats."]
    assert "private auth detail" not in warnings[0]


def test_readiness_is_silent_when_version_and_login_status_succeed() -> None:
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        return CompletedProcess(command, 0, "ready", "")

    assert _readiness_warnings(which=lambda name: "/bin/codex", run=run) == []
    assert commands == [["/bin/codex", "--version"], ["/bin/codex", "login", "status"]]


@pytest.mark.parametrize(
    "args",
    [
        ["--yolo"],
        ["--yolo=true"],
        ["--dangerously-bypass-approvals-and-sandbox"],
        ["--dangerously-bypass-hook-trust"],
        ["--sandbox", "danger-full-access"],
        ["--sandbox=danger-full-access"],
        ["-c", 'sandbox_mode="danger-full-access"'],
        ["-c", "bypass_hook_trust=true"],
        ["--config", "bypass_hook_trust=true"],
        ["--config=bypass_hook_trust=true"],
        ["-c", "features.hooks=false"],
        ["--config", "features={hooks=false}"],
        ["-cfeatures.hooks=false"],
        ["-cfeatures.codex_hooks=false"],
        ["-cbypass_hook_trust=true"],
        ["-c", "allow_managed_hooks_only=true"],
        ["--config", "hooks.PreToolUse=[]"],
        ["--disable", "hooks"],
        ["--disable=hooks"],
        ["--disable", "codex_hooks"],
        ["--disable=codex_hooks"],
    ],
)
def test_get_cli_command_rejects_dangerous_passthrough(args: list[str]) -> None:
    with pytest.raises(ValueError, match="unsafe Codex option"):
        CodexProvider().get_cli_command(args)


def test_hitl_delivers_role_rules_and_skill_index_via_toml(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    skill = _make_skill(tmp_path, "release", "Prepare a safe release")
    rule = _make_rule(tmp_path)

    args, env, persisted = CodexProvider().build_session_prompt(
        project, _fake_result(skills=[skill], rules=[rule]), "sid-a"
    )

    delivered = _developer_instructions(args)
    assert delivered == persisted
    assert "You are the repository maintainer." in delivered
    assert "Never bypass repository safety checks." in delivered
    assert "## AVAILABLE SKILLS" in delivered
    skill_md = CodexProvider().session_skills_root(project, "sid-a") / "release" / "SKILL.md"
    assert str(skill_md) in delivered
    assert "Prepare a safe release" in delivered
    # This fixture skill declares no runtime hook, so the surface must not make
    # the user trust an inert dispatcher or publish hook-only environment pins.
    assert "AI_HATS_SESSION_CACHE_DIR" not in env
    assert "AI_HATS_PYTHON" not in env
    assert CodexProvider().get_env(tmp_path / "session", project)["AI_HATS_PROJECT_DIR"] == str(
        project
    )


def test_hitl_argv_has_safe_permissions_and_keeps_worktree_cwd(tmp_path: Path) -> None:
    project = tmp_path / "canonical-main"
    project.mkdir()
    args, _, _ = CodexProvider().build_session_prompt(project, _fake_result(), "sid")
    command = CodexProvider().get_cli_launch_args(["codex", *args], "sid", False)

    assert command[0] == "codex"
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("--ask-for-approval") + 1] == "on-request"
    # WrapRunner's actual process cwd may be an ai-hats worktree while project_dir
    # is the canonical/main checkout. Emitting -C here would silently leave the WT.
    assert "-C" not in command and "--cd" not in command
    assert not any("dangerously-bypass" in part or part == "--yolo" for part in command)


@pytest.mark.parametrize(
    ("extra", "sandbox", "approval"),
    [
        (["--sandbox", "read-only", "--ask-for-approval", "never"], "read-only", "never"),
        (["--sandbox=read-only", "-a", "never"], "read-only", "never"),
        (["-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"'], None, None),
        (
            [
                "--config",
                'sandbox_mode="read-only"',
                '--config=approval_policy="never"',
            ],
            None,
            None,
        ),
        (
            ["--config", '"sandbox_mode"="read-only"', '-c="approval_policy"="never"'],
            None,
            None,
        ),
        (
            ['-csandbox_mode="read-only"', '-capproval_policy="never"'],
            None,
            None,
        ),
    ],
)
def test_explicit_safe_policy_replaces_defaults_without_duplicate_codex_flags(
    tmp_path: Path,
    extra: list[str],
    sandbox: str | None,
    approval: str | None,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    provider = CodexProvider()
    session_args, _, _ = provider.build_session_prompt(project, _fake_result(), "sid")

    command = provider.get_cli_launch_args(
        provider.get_cli_command(extra) + session_args,
        "sid",
        False,
    )

    sandbox_tokens = [
        token for token in command if token in {"--sandbox", "-s"} or token.startswith("--sandbox=")
    ]
    approval_tokens = [
        token
        for token in command
        if token in {"--ask-for-approval", "-a"} or token.startswith("--ask-for-approval=")
    ]
    if sandbox is None:
        assert sandbox_tokens == []
        assert any("sandbox_mode" in value for value in command)
    else:
        assert len(sandbox_tokens) == 1
        assert sandbox in command or f"--sandbox={sandbox}" in command
    if approval is None:
        assert approval_tokens == []
        assert any("approval_policy" in value for value in command)
    else:
        assert len(approval_tokens) == 1
        assert approval in command or f"--ask-for-approval={approval}" in command


def test_skill_materialization_is_session_scoped_and_clean_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    skill = _make_skill(tmp_path, "release")

    CodexProvider().build_session_prompt(project, _fake_result(skills=[skill]), "sid-a")

    delivered = CodexProvider().session_skills_root(project, "sid-a") / "release" / "SKILL.md"
    assert delivered.is_file()
    assert list(project.iterdir()) == []


def test_role_skills_activate_native_codex_home_with_shared_user_state(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    base_home = tmp_path / "user-codex-home"
    (base_home / "auth.json").write_text("shared auth")
    (base_home / "config.toml").write_text("shared config")
    sqlite_artifacts = {
        base_home / "state_5.sqlite",
        base_home / "state_5.sqlite-shm",
        base_home / "state_5.sqlite-wal",
        base_home / "state_5.sqlite-journal",
    }
    for path in sqlite_artifacts:
        path.write_text("shared sqlite state")
    skill = _make_skill(tmp_path, "release")

    artifacts = CodexProvider().build_session_artifacts(
        project,
        _fake_result(skills=[skill]),
        "sid-native",
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )

    session_home = session_cache_dir(project, "sid-native") / "codex-home"
    assert artifacts.extra_env["CODEX_HOME"] == str(session_home)
    assert artifacts.extra_env["CODEX_SQLITE_HOME"] == str(base_home)
    assert artifacts.extra_env["AI_HATS_CODEX_BASE_HOME"] == str(base_home)
    assert (session_home / "skills" / "release" / "SKILL.md").is_file()
    assert (session_home / "auth.json").is_symlink()
    assert (session_home / "auth.json").resolve() == base_home / "auth.json"
    assert (session_home / "config.toml").is_symlink()
    assert not any((session_home / path.name).exists() for path in sqlite_artifacts)
    assert list(project.iterdir()) == []


def test_role_skills_override_collisions_and_preserve_other_base_skills(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    base_skills = tmp_path / "user-codex-home" / "skills"
    for name in (".system", "personal", "release"):
        source = base_skills / name
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"base {name}")
    role_skill = _make_skill(tmp_path, "release")

    CodexProvider().build_session_prompt(project, _fake_result(skills=[role_skill]), "sid-merged")

    skills_root = CodexProvider().session_skills_root(project, "sid-merged")
    assert (skills_root / ".system").is_symlink()
    assert (skills_root / "personal").is_symlink()
    assert not (skills_root / "release").is_symlink()
    assert (skills_root / "release" / "SKILL.md").read_text().endswith("Instructions.\n")
    assert (base_skills / "release" / "SKILL.md").read_text() == "base release"


def test_empty_role_skills_do_not_activate_codex_home_overlay(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    provider = CodexProvider()

    artifacts = provider.build_session_artifacts(
        project,
        _fake_result(),
        "sid-empty",
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )

    assert "CODEX_HOME" not in artifacts.extra_env
    assert "CODEX_SQLITE_HOME" not in artifacts.extra_env
    assert not provider.session_codex_home(project, "sid-empty").exists()


def test_nested_launch_keeps_original_base_home_and_explicit_sqlite_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    base_home = tmp_path / "original-codex-home"
    base_home.mkdir()
    (base_home / "config.toml").write_text("shared config")
    outer_overlay = tmp_path / "outer-codex-home"
    outer_overlay.mkdir()
    sqlite_home = tmp_path / "sqlite-home"
    sqlite_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(outer_overlay))
    monkeypatch.setenv("AI_HATS_CODEX_BASE_HOME", str(base_home))
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(sqlite_home))

    artifacts = CodexProvider().build_session_artifacts(
        project,
        _fake_result(skills=[_make_skill(tmp_path, "release")]),
        "sid-nested",
        run_mode=RunMode.AUTOMATE,
        artifacts=BuiltArtifacts(),
    )

    session_home = CodexProvider().session_codex_home(project, "sid-nested")
    assert artifacts.extra_env["AI_HATS_CODEX_BASE_HOME"] == str(base_home)
    assert artifacts.extra_env["CODEX_SQLITE_HOME"] == str(sqlite_home)
    assert (session_home / "config.toml").resolve() == base_home / "config.toml"
    assert (session_home / "config.toml").resolve() != outer_overlay / "config.toml"


def test_recursive_base_home_is_rejected_before_session_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    cache_home = tmp_path / "cache-home"
    cache_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(cache_home))
    provider = CodexProvider()

    with pytest.raises(
        RuntimeError, match="Codex base home must be outside the ai-hats session home"
    ) as raised:
        provider.build_session_prompt(
            project,
            _fake_result(skills=[_make_skill(tmp_path, "release")]),
            "sid-recursive",
        )

    assert str(cache_home) not in str(raised.value)
    assert not provider.session_codex_home(project, "sid-recursive").exists()


def test_parallel_sessions_have_disjoint_skill_trees(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    skill_a = _make_skill(tmp_path, "skill-a")
    skill_b = _make_skill(tmp_path, "skill-b")
    provider = CodexProvider()

    provider.build_session_prompt(project, _fake_result(skills=[skill_a]), "sid-a")
    provider.build_session_prompt(project, _fake_result(skills=[skill_b]), "sid-b")

    root_a = provider.session_skills_root(project, "sid-a")
    root_b = provider.session_skills_root(project, "sid-b")
    assert root_a != root_b
    assert (root_a / "skill-a" / "SKILL.md").is_file()
    assert not (root_a / "skill-b").exists()
    assert (root_b / "skill-b" / "SKILL.md").is_file()
    assert not (root_b / "skill-a").exists()
    assert list(project.iterdir()) == []


def test_automate_uses_exec_json_ephemeral_and_never_approval(tmp_path: Path) -> None:
    project = tmp_path / "canonical-main"
    project.mkdir()
    provider = CodexProvider()
    artifacts = provider.build_session_artifacts(
        project,
        _fake_result(),
        "sid-auto",
        run_mode=RunMode.AUTOMATE,
        artifacts=BuiltArtifacts(),
    )

    described = provider.describe_automate_launch(
        project,
        _fake_result(),
        "sid-auto",
        artifacts,
        task="Change one file",
        ticket_id="",
        model="gpt-5.4",
        env={},
    )

    command = described.launch
    assert command[0] == "codex"
    exec_index = command.index("exec")
    # Codex 0.147 accepts approval/sandbox/config/model on the global parser,
    # before the `exec` subcommand. `exec`-local flags follow the subcommand.
    for flag in ("-c", "--sandbox", "--ask-for-approval", "--model"):
        assert command.index(flag) < exec_index
    assert command[exec_index : exec_index + 3] == ["exec", "--json", "--ephemeral"]
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("--ask-for-approval") + 1] == "never"
    assert command[command.index("--model") + 1] == "gpt-5.4"
    assert command[-1] == described.prompt
    assert "# TASK\nChange one file" in described.prompt
    # Role is a developer instruction, not duplicated into the user task prompt.
    assert "repository maintainer" not in described.prompt
    assert "repository maintainer" in _developer_instructions(command)
    # SubAgentRunner supplies the isolated worktree as subprocess cwd.
    assert "-C" not in command and "--cd" not in command
    assert "--yolo" not in command


def test_get_run_command_replaces_hitl_approval_before_exec() -> None:
    command = CodexProvider().get_run_command(
        ["codex", "--sandbox", "workspace-write", "--ask-for-approval", "on-request"],
        "task",
    )
    exec_index = command.index("exec")
    approval_index = command.index("--ask-for-approval")
    assert approval_index < exec_index
    assert command[approval_index + 1] == "never"
    assert command[exec_index:] == ["exec", "--json", "--ephemeral", "task"]


def test_automate_materializes_skills_without_project_writes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    skill = _make_skill(tmp_path, "review")
    artifacts = CodexProvider().build_session_artifacts(
        project,
        _fake_result(skills=[skill]),
        "sid-auto",
        run_mode=RunMode.AUTOMATE,
        artifacts=BuiltArtifacts(),
    )

    skills_root = CodexProvider().session_skills_root(project, "sid-auto")
    assert (skills_root / "review" / "SKILL.md").is_file()
    assert skills_root in artifacts.materialized
    assert str(skills_root / "review" / "SKILL.md") in (artifacts.full_content or "")
    assert list(project.iterdir()) == []
