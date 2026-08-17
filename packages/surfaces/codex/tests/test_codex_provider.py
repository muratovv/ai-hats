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
    skill_md = session_cache_dir(project, "sid-a") / "skills" / "release" / "SKILL.md"
    assert str(skill_md) in delivered
    assert "Prepare a safe release" in delivered
    # This fixture skill declares no runtime hook, so the surface must not make
    # the user trust an inert dispatcher or publish hook-only environment pins.
    assert env == {}
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

    delivered = session_cache_dir(project, "sid-a") / "skills" / "release" / "SKILL.md"
    assert delivered.is_file()
    assert list(project.iterdir()) == []


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

    skills_root = session_cache_dir(project, "sid-auto") / "skills"
    assert (skills_root / "review" / "SKILL.md").is_file()
    assert skills_root in artifacts.materialized
    assert str(skills_root / "review" / "SKILL.md") in (artifacts.full_content or "")
    assert list(project.iterdir()) == []
