"""Contract tests for the Codex CLI surface: a session planned from the
composition half and the probed home, applied, and its resources claimed."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import tomllib
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.fs_digest import dir_digest
from ai_hats.session_artifacts import RunMode, SessionPolicy, assemble_brief
from ai_hats.session_plan import probe_host
from ai_hats.session_run import SessionRun
from ai_hats.surfaces import apply, context_text, validate
from ai_hats.surfaces.codex import CodexSurface
from ai_hats.surfaces.codex.home import CodexHome
from ai_hats.surfaces.codex.provider import _readiness_warnings
from ai_hats.surfaces.plan import (
    CompositionPlan,
    EscapeUndeclared,
    Hooks,
    Host,
    LaunchFlags,
    MaterializationPlan,
    Prompt,
    PromptBlock,
    PromptMember,
    Skill,
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    codex_home = tmp_path / "user-codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)
    monkeypatch.delenv("AI_HATS_CODEX_BASE_HOME", raising=False)


def _skill(tmp_path: Path, name: str, description: str = "Test skill") -> Skill:
    source = tmp_path / "skill-sources" / name
    source.mkdir(parents=True)
    document = f"---\nname: {name}\ndescription: {description}\n---\n# {name}\nInstructions.\n"
    (source / "SKILL.md").write_text(document)
    return Skill(
        name=f"skills::{name}",
        path=source.resolve(),
        content_digest=dir_digest(source),
        document=document,
    )


def _composition(*skills: Skill, rule: str | None = None) -> CompositionPlan:
    blocks = [
        PromptBlock("PRIORITIES", (PromptMember("test::priorities", "1. Reliability", None),)),
        PromptBlock(
            None,
            (PromptMember("test::prompt", "## ROLE\nYou are the repository maintainer.", None),),
        ),
    ]
    if rule is not None:
        blocks.append(
            PromptBlock("RULES", (PromptMember("rules::safe-edits", rule, "safe-edits"),))
        )
    return CompositionPlan(
        identity="test", prompt=Prompt(tuple(blocks)), skills=skills, hooks=Hooks((), ()), trace=()
    )


def _layout(tmp_path: Path, name: str = "project") -> ProjectLayout:
    project = tmp_path / name
    project.mkdir(exist_ok=True)
    return ProjectLayout.at(project)


def _plan(
    layout: ProjectLayout,
    composition: CompositionPlan,
    session_id: str,
    *,
    run_mode: RunMode = RunMode.HITL,
    policy: SessionPolicy = SessionPolicy(),
) -> MaterializationPlan:
    surface = CodexSurface()
    return surface.plan(
        composition,
        run_mode=run_mode,
        policy=policy,
        root=layout.cache.session(session_id),
        layout=layout,
        host=probe_host(surface=surface),
    )


def _flags(layout: ProjectLayout, session_id: str, **overrides) -> LaunchFlags:
    given = dict(
        session_id=session_id,
        session_dir=layout.sessions.runs / session_id,
        trace_path="t",
        root_pid="1",
        provider_session_id=None,
    )
    given.update(overrides)
    return LaunchFlags(**given)


def _session(
    layout: ProjectLayout, composition: CompositionPlan, session_id: str, run: SessionRun
) -> MaterializationPlan:
    """The runner's sequence for one session: plan, apply, claim."""
    plan = _plan(layout, composition, session_id)
    apply(plan)
    CodexSurface().claim_resources(plan, _flags(layout, session_id), layout=layout, run=run)
    return plan


def _session_home(layout: ProjectLayout, session_id: str) -> Path:
    return CodexSurface().session_codex_home(layout, session_id)


def _developer_instructions(args: list[str]) -> str:
    index = args.index("-c")
    parsed = tomllib.loads(args[index + 1])
    return parsed["developer_instructions"]


def _outside_root(plan: MaterializationPlan):
    return [e for e in plan.entries if not e.target.is_relative_to(plan.root)]


def test_provider_identity_and_inline_only_contract(tmp_path: Path) -> None:
    provider = CodexSurface()
    assert provider.name == "codex"
    assert provider.detected_home_dirs() == [".codex"]
    assert provider.supports_session_command_wrappers() is True
    assert provider.system_prompt_path(ProjectLayout.at(tmp_path)) is None
    assert provider.rules_dir(tmp_path / "session") == tmp_path / "session" / "rules"


def test_get_cli_command_preserves_safe_passthrough() -> None:
    provider = CodexSurface()
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
        CodexSurface().get_cli_command(args)


# ── the plan ──────────────────────────────────────────────────────────────────


def test_planning_without_the_probed_home_is_refused(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    host = Host(python=Path("/opt/py"), path="/usr/bin", commands={})

    with pytest.raises(RuntimeError, match="probe_host"):
        CodexSurface().plan(
            _composition(),
            run_mode=RunMode.HITL,
            policy=SessionPolicy(),
            root=layout.cache.session("sid"),
            layout=layout,
            host=host,
        )


def test_hitl_delivers_role_rules_and_skill_index_via_toml(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    skill = _skill(tmp_path, "release", "Prepare a safe release")

    plan = _plan(
        layout, _composition(skill, rule="Never bypass repository safety checks."), "sid-a"
    )

    delivered = _developer_instructions(list(plan.launch.args))
    assert delivered == plan.prompt.text == context_text(plan)
    assert plan.context is None, "the role rides the argv, not a file"
    assert "You are the repository maintainer." in delivered
    assert "Never bypass repository safety checks." in delivered
    assert "## AVAILABLE SKILLS" in delivered
    skill_md = _session_home(layout, "sid-a") / "skills" / "release" / "SKILL.md"
    assert str(skill_md) in delivered
    assert "Prepare a safe release" in delivered
    # This skill declares no runtime hook, so the surface must not make the
    # user trust an inert dispatcher or publish hook-only environment pins.
    assert "AI_HATS_SESSION_CACHE_DIR" not in plan.env
    assert "AI_HATS_PYTHON" not in plan.env
    assert not any(a.startswith("hooks.") for a in plan.launch.args)
    assert plan.env["AI_HATS_PROJECT_DIR"] == str(layout.root)


def test_hitl_argv_has_safe_permissions_and_keeps_worktree_cwd(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "canonical-main")
    plan = _plan(layout, _composition(), "sid")
    command = CodexSurface().get_cli_launch_args(["codex", *plan.launch.args], "sid", False)

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
    layout = _layout(tmp_path)
    provider = CodexSurface()
    session_args = list(_plan(layout, _composition(), "sid").launch.args)

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


def test_settings_off_leaves_the_sandbox_and_approval_to_the_person(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _plan(layout, _composition(), "sid", policy=SessionPolicy(settings=False))
    assert "--sandbox" not in plan.launch.args and "--ask-for-approval" not in plan.launch.args


def test_skill_materialization_is_session_scoped_and_clean_root(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _plan(layout, _composition(_skill(tmp_path, "release")), "sid-a")

    apply(plan)

    delivered = _session_home(layout, "sid-a") / "skills" / "release" / "SKILL.md"
    assert delivered.is_file()
    assert delivered.read_text().endswith("Instructions.\n")
    assert list(layout.root.iterdir()) == []


def test_the_plan_declares_every_write_outside_the_root(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _plan(layout, _composition(_skill(tmp_path, "release")), "sid-escape")

    validate(plan)
    outside = _outside_root(plan)
    assert outside, "the session home lives beside the person's Codex home, not under the root"
    assert all(e.escape for e in outside)
    base_home = (tmp_path / "user-codex-home").resolve()
    assert all(e.target.is_relative_to(base_home) for e in outside)
    inside = [e for e in plan.entries if e.target.is_relative_to(plan.root)]
    assert inside and not any(e.escape for e in inside)

    undeclared = dataclasses.replace(outside[0], escape=False)
    entries = tuple(undeclared if e is outside[0] else e for e in plan.entries)
    with pytest.raises(EscapeUndeclared):
        validate(dataclasses.replace(plan, entries=entries))


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_planning_for_one_root_is_the_same_before_and_after_it_is_applied(
    tmp_path: Path, run_mode: RunMode
) -> None:
    layout = _layout(tmp_path)
    base_home = tmp_path / "user-codex-home"
    (base_home / "config.toml").write_text("shared config")
    (base_home / "auth.json").write_text("shared auth")
    composition = _composition(_skill(tmp_path, "release"))

    before = _plan(layout, composition, "sid-pure", run_mode=run_mode)
    apply(before)
    after = _plan(layout, composition, "sid-pure", run_mode=run_mode)

    assert before == after and before.digest == after.digest
    extra = dataclasses.replace(composition.skills[0], name="skills::extra")
    more = dataclasses.replace(composition, skills=(*composition.skills, extra))
    assert _plan(layout, more, "sid-pure", run_mode=run_mode) != before, "a changed input must show"


def test_applying_the_plan_twice_changes_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (tmp_path / "user-codex-home" / "auth.json").write_text("shared auth")
    plan = _plan(layout, _composition(_skill(tmp_path, "release")), "sid-twice")

    first = apply(plan)
    second = apply(plan)

    assert first.changed is True and second.changed is False
    assert {a.outcome.value for a in second.entries} == {"unchanged"}


def test_role_skills_activate_native_codex_home_with_shared_user_state(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
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

    plan = _plan(layout, _composition(_skill(tmp_path, "release")), "sid-native")
    apply(plan)

    session_home = base_home / ".ai-hats" / "session-homes" / layout.cache.root.name / "sid-native"
    assert plan.env["CODEX_HOME"] == str(session_home)
    assert plan.env["CODEX_SQLITE_HOME"] == str(base_home)
    assert plan.env["AI_HATS_CODEX_BASE_HOME"] == str(base_home)
    assert json.loads((session_home / ".ai-hats-session.json").read_text()) == {
        "base_home": str(base_home),
        "project_key": layout.cache.root.name,
        "session_id": "sid-native",
        "sqlite_home": str(base_home),
        "version": 1,
    }
    assert (session_home / "skills" / "release" / "SKILL.md").is_file()
    assert (session_home / "sessions").is_symlink()
    assert (session_home / "sessions").resolve() == base_home / "sessions"
    assert not (session_home / "auth.json").is_symlink()
    assert (session_home / "auth.json").read_text() == "shared auth"
    assert (session_home / "auth.json").stat().st_mode & 0o777 == 0o600
    assert (session_home / "config.toml").is_symlink()
    assert not any((session_home / path.name).exists() for path in sqlite_artifacts)
    assert layout.cache.session("sid-native") not in session_home.parents
    assert list(layout.root.iterdir()) == []
    # The credential's bytes are in no record: a private copy, and a baseline
    # that names the digest the person's file had when the session was planned.
    staged = next(e for e in plan.entries if e.target == session_home / "auth.json")
    assert staged.kind.value == "copy_file" and staged.private and staged.digest is None


def test_session_run_normalizes_rollout_and_removes_home(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    base_home = tmp_path / "user-codex-home"
    rollout = base_home / "sessions/2026/08/24/rollout.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("thread")
    database = base_home / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
    run = SessionRun(SimpleNamespace(session_id="sid-cleanup", log_sys=lambda _message: None))
    with run:
        _session(layout, _composition(_skill(tmp_path, "release")), "sid-cleanup", run)
        session_home = _session_home(layout, "sid-cleanup")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO threads VALUES (?, ?)",
                (
                    "thread",
                    str(session_home / "sessions" / rollout.relative_to(base_home / "sessions")),
                ),
            )

    assert not session_home.exists()
    with sqlite3.connect(database) as connection:
        [(stored_path,)] = connection.execute("SELECT rollout_path FROM threads")
    assert stored_path == str(rollout)


def test_session_logout_removes_shared_auth(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    base_home = tmp_path / "user-codex-home"
    (base_home / "auth.json").write_text('{"fixture": "old"}')
    run = SessionRun(SimpleNamespace(session_id="logout", log_sys=lambda _message: None))
    with run:
        _session(layout, _composition(_skill(tmp_path, "release")), "logout", run)
        session_home = _session_home(layout, "logout")
        (session_home / "auth.json").unlink()  # safe-delete: ok synthetic logout fixture

    assert not (base_home / "auth.json").exists()


def test_session_without_auth_baseline_retains_credentials(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    base_home = tmp_path / "user-codex-home"
    (base_home / "auth.json").write_text('{"fixture": "old"}')
    notices: list[str] = []
    run = SessionRun(SimpleNamespace(session_id="missing-baseline", log_sys=notices.append))
    with run:
        _session(layout, _composition(_skill(tmp_path, "release")), "missing-baseline", run)
        session_home = _session_home(layout, "missing-baseline")
        (session_home / "auth.json").write_text('{"fixture": "new"}')
        baseline = session_home / ".ai-hats-auth-baseline.json"
        baseline.unlink()  # safe-delete: ok synthetic baseline fixture

    assert (session_home / "auth.json").read_text() == '{"fixture": "new"}'
    assert (base_home / "auth.json").read_text() == '{"fixture": "old"}'
    assert len(notices) == 1
    assert "baseline" in notices[0]


def test_session_logout_preserves_a_newer_login(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    base_home = tmp_path / "user-codex-home"
    (base_home / "auth.json").write_text('{"fixture": "old"}')
    notices: list[str] = []
    run = SessionRun(SimpleNamespace(session_id="older", log_sys=notices.append))
    with run:
        _session(layout, _composition(_skill(tmp_path, "release")), "older", run)
        session_home = _session_home(layout, "older")
        (session_home / "auth.json").unlink()  # safe-delete: ok synthetic logout fixture
        (base_home / "auth.json").write_text('{"fixture": "newer"}')

    assert (base_home / "auth.json").read_text() == '{"fixture": "newer"}'
    assert session_home.exists()
    assert len(notices) == 1
    assert "changed in another session" in notices[0]


def test_session_run_retains_home_when_rollout_target_is_missing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    base_home = tmp_path / "user-codex-home"
    database = base_home / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
    notices: list[str] = []
    run = SessionRun(SimpleNamespace(session_id="sid-retained", log_sys=notices.append))

    with run:
        _session(layout, _composition(_skill(tmp_path, "release")), "sid-retained", run)
        session_home = _session_home(layout, "sid-retained")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO threads VALUES (?, ?)",
                ("thread", str(session_home / "sessions/missing-rollout.jsonl")),
            )

    assert session_home.is_dir()
    assert len(notices) == 1
    assert "retained: 1 rollout reference(s) remain" in notices[0]


def test_claiming_resources_reconciles_a_crashed_home_without_a_session_cache(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    base_home = tmp_path / "user-codex-home"
    rollout = base_home / "sessions/2026/08/24/rollout.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("thread")
    database = base_home / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
    # A session that applied its plan and never closed: no claim, no finalizer.
    apply(_plan(layout, _composition(_skill(tmp_path, "release")), "sid-crashed"))
    session_home = _session_home(layout, "sid-crashed")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO threads VALUES (?, ?)",
            (
                "thread",
                str(session_home / "sessions" / rollout.relative_to(base_home / "sessions")),
            ),
        )
    layout.cache.session("sid-crashed").rmdir()  # safe-delete: ok empty-dir

    run = SessionRun(SimpleNamespace(session_id="sid-current", log_sys=lambda _message: None))
    with run:
        _session(layout, _composition(), "sid-current", run)

    assert not session_home.exists()
    with sqlite3.connect(database) as connection:
        [(stored_path,)] = connection.execute("SELECT rollout_path FROM threads")
    assert stored_path == str(rollout)


def test_claiming_resources_skips_a_home_with_a_live_session_cache(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    base_home = tmp_path / "user-codex-home"
    rollout = base_home / "sessions/2026/08/24/rollout.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("thread")
    database = base_home / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT)")
    apply(_plan(layout, _composition(_skill(tmp_path, "release")), "sid-running"))
    session_home = _session_home(layout, "sid-running")
    stored_path = str(session_home / "sessions" / rollout.relative_to(base_home / "sessions"))
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO threads VALUES (?, ?)", ("thread", stored_path))

    notices: list[str] = []
    run = SessionRun(SimpleNamespace(session_id="sid-current", log_sys=notices.append))
    with run:
        _session(layout, _composition(), "sid-current", run)

    assert notices == []
    assert session_home.is_dir()
    with sqlite3.connect(database) as connection:
        [(remaining_path,)] = connection.execute("SELECT rollout_path FROM threads")
    assert remaining_path == stored_path


def test_claiming_resources_reports_a_recovery_failure(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    class RecoveryFailureProvider(CodexSurface):
        def _recover_session_homes(self, _layout, _session_id: str) -> list[str]:
            raise OSError("managed root unavailable")

    notices: list[str] = []
    run = SessionRun(SimpleNamespace(session_id="sid-current", log_sys=notices.append))
    with run:
        plan = _plan(layout, _composition(), "sid-current")
        apply(plan)
        RecoveryFailureProvider().claim_resources(
            plan, _flags(layout, "sid-current"), layout=layout, run=run
        )

    assert notices == ["Codex session-home recovery failed: OSError: managed root unavailable"]


def test_claiming_resources_defers_the_home_only_when_the_plan_has_one(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    surface = CodexSurface()
    deferred: list[str] = []
    run = SimpleNamespace(defer=lambda name, _cleanup: deferred.append(name), warn=pytest.fail)

    with_home = _plan(layout, _composition(_skill(tmp_path, "release")), "sid-home")
    surface.claim_resources(with_home, _flags(layout, "sid-home"), layout=layout, run=run)
    without = _plan(layout, _composition(), "sid-bare")
    surface.claim_resources(without, _flags(layout, "sid-bare"), layout=layout, run=run)

    assert deferred == ["Codex session home"]


def test_role_skills_override_collisions_and_preserve_other_base_skills(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    base_skills = tmp_path / "user-codex-home" / "skills"
    for name in (".system", "personal", "release"):
        source = base_skills / name
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"base {name}")

    plan = _plan(layout, _composition(_skill(tmp_path, "release")), "sid-merged")
    apply(plan)

    skills_root = CodexSurface().session_skills_root(layout, "sid-merged")
    assert (skills_root / ".system").is_symlink()
    assert (skills_root / "personal").is_symlink()
    assert not (skills_root / "release").is_symlink()
    assert (skills_root / "release" / "SKILL.md").read_text().endswith("Instructions.\n")
    assert (base_skills / "release" / "SKILL.md").read_text() == "base release"


def test_empty_role_skills_do_not_activate_codex_home_overlay(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    plan = _plan(layout, _composition(), "sid-empty")
    apply(plan)

    assert "CODEX_HOME" not in plan.env
    assert "CODEX_SQLITE_HOME" not in plan.env
    assert _outside_root(plan) == []
    assert not _session_home(layout, "sid-empty").exists()


def test_nested_launch_keeps_original_base_home_and_explicit_sqlite_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
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

    plan = _plan(
        layout, _composition(_skill(tmp_path, "release")), "sid-nested", run_mode=RunMode.AUTOMATE
    )
    apply(plan)

    session_home = _session_home(layout, "sid-nested")
    assert plan.env["AI_HATS_CODEX_BASE_HOME"] == str(base_home)
    assert plan.env["CODEX_SQLITE_HOME"] == str(sqlite_home)
    assert (session_home / "config.toml").resolve() == base_home / "config.toml"
    assert (session_home / "config.toml").resolve() != outer_overlay / "config.toml"


def test_the_probed_home_is_the_one_fact_of_the_person_a_plan_carries(tmp_path: Path) -> None:
    base_home = tmp_path / "user-codex-home"
    (base_home / "config.toml").write_text("shared config")
    (base_home / "state_5.sqlite").write_text("db")
    (base_home / "skills" / "personal").mkdir(parents=True)
    (base_home / "auth.json").write_text("shared auth")

    home = CodexSurface().probe_home(os.environ)

    assert home == CodexHome(
        base_home=base_home.resolve(),
        sqlite_home=base_home.resolve(),
        # `sessions` before it exists: the plan creates and links it in one go.
        entries=("config.toml", "sessions"),
        skills=("personal",),
        auth_digest=home.auth_digest,
    )
    assert home.auth_digest is not None and len(home.auth_digest) == 64
    (base_home / "auth.json").unlink()  # safe-delete: ok synthetic logout fixture
    assert CodexSurface().probe_home(os.environ).auth_digest is None


def test_a_missing_base_home_is_refused_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nowhere"))

    with pytest.raises(RuntimeError, match="existing absolute directory"):
        CodexSurface().probe_home(os.environ)

    assert not (tmp_path / "nowhere").exists()


def test_cache_backed_base_home_is_rejected_before_session_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_home = tmp_path / "cache-home"
    cache_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(cache_home))

    with pytest.raises(
        RuntimeError, match="Codex base home must be disjoint from the ai-hats cache home"
    ) as raised:
        CodexSurface().probe_home(os.environ)

    assert str(cache_home) not in str(raised.value)
    assert not (cache_home / ".ai-hats").exists()


def test_cache_backed_sqlite_home_is_rejected_before_session_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_home = tmp_path / "cache-home"
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(cache_home / "sqlite"))

    with pytest.raises(
        RuntimeError, match="Codex SQLite home must be outside the ai-hats cache home"
    ):
        CodexSurface().probe_home(os.environ)

    assert not (tmp_path / "user-codex-home" / ".ai-hats").exists()


def test_managed_session_sqlite_home_is_rejected_before_session_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_home = tmp_path / "user-codex-home"
    monkeypatch.setenv(
        "CODEX_SQLITE_HOME", str(base_home / ".ai-hats" / "session-homes" / "sqlite")
    )

    with pytest.raises(
        RuntimeError, match="Codex SQLite home must be outside managed session homes"
    ):
        CodexSurface().probe_home(os.environ)

    assert not (base_home / ".ai-hats").exists()


def test_parallel_sessions_have_disjoint_skill_trees(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    provider = CodexSurface()

    apply(_plan(layout, _composition(_skill(tmp_path, "skill-a")), "sid-a"))
    apply(_plan(layout, _composition(_skill(tmp_path, "skill-b")), "sid-b"))

    root_a = provider.session_skills_root(layout, "sid-a")
    root_b = provider.session_skills_root(layout, "sid-b")
    assert root_a != root_b
    assert (root_a / "skill-a" / "SKILL.md").is_file()
    assert not (root_a / "skill-b").exists()
    assert (root_b / "skill-b" / "SKILL.md").is_file()
    assert not (root_b / "skill-a").exists()
    assert list(layout.root.iterdir()) == []


def test_automate_uses_exec_json_ephemeral_and_never_approval(tmp_path: Path) -> None:
    layout = _layout(tmp_path, "canonical-main")
    provider = CodexSurface()
    plan = _plan(layout, _composition(), "sid-auto", run_mode=RunMode.AUTOMATE)
    flags = _flags(
        layout,
        "sid-auto",
        model="gpt-5.4",
        brief=assemble_brief(layout, task="Change one file", ticket_id=""),
    )

    launched = provider.automate_launch(plan, flags, {}, layout=layout)

    command = list(launched.args)
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
    assert command[-1] == launched.prompt
    assert launched.prompt.startswith("# WORKING_DIRECTORY\n")
    assert "# TASK\nChange one file" in launched.prompt
    # Role is a developer instruction, not duplicated into the user task prompt.
    assert "repository maintainer" not in launched.prompt
    assert "repository maintainer" in _developer_instructions(command)
    # SubAgentRunner supplies the isolated worktree as subprocess cwd.
    assert "-C" not in command and "--cd" not in command
    assert "--yolo" not in command
    assert provider.describe_launch(launched) == command


def test_get_run_command_replaces_hitl_approval_before_exec() -> None:
    command = CodexSurface().get_run_command(
        ["codex", "--sandbox", "workspace-write", "--ask-for-approval", "on-request"],
        "task",
    )
    exec_index = command.index("exec")
    approval_index = command.index("--ask-for-approval")
    assert approval_index < exec_index
    assert command[approval_index + 1] == "never"
    assert command[exec_index:] == ["exec", "--json", "--ephemeral", "task"]


def test_automate_materializes_skills_without_project_writes(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _plan(
        layout, _composition(_skill(tmp_path, "review")), "sid-auto", run_mode=RunMode.AUTOMATE
    )
    apply(plan)

    skills_root = CodexSurface().session_skills_root(layout, "sid-auto")
    assert (skills_root / "review" / "SKILL.md").is_file()
    assert str(skills_root / "review" / "SKILL.md") in plan.prompt.text
    assert list(layout.root.iterdir()) == []
