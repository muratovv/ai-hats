"""In-process CLI tests for full system initialization flow (CliRunner-based)."""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.resolver import LibraryResolver
from ai_hats.models import ComponentType
from ai_hats.paths import rules_dir, skills_dir, tasks_dir


def _all_roles() -> list[str]:
    """Discover all roles from the built-in library (core + usage)."""
    from ai_hats.assembler import _builtin_library_layers

    resolver = LibraryResolver(_builtin_library_layers())
    return sorted(resolver.list_components(ComponentType.ROLE))


ALL_ROLES = _all_roles()


@pytest.fixture()
def cli_project(tmp_path, monkeypatch):
    """Clean project directory with chdir and CliRunner."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project, CliRunner()


def _pin_edge(project):
    """HATS-764: append a `harness: channel: edge` block so `self update` takes
    the git ahead/diverged + in-place path these pre-channel tests assert,
    instead of the new default-stable PyPI path (which 404s — name unpublished
    until HATS-765). Channel-specific routing has its own dedicated tests."""
    p = project / "ai-hats.yaml"
    p.write_text(p.read_text() + "harness:\n  channel: edge\n")


def test_set_creates_project(cli_project):
    """ai-hats config set -r <role> -p claude auto-inits and creates all artifacts."""
    project, runner = cli_project

    result = runner.invoke(main, ["config", "set", "-r", ALL_ROLES[0], "-p", "claude"])

    assert result.exit_code == 0, result.output
    assert (project / "ai-hats.yaml").exists()
    assert (rules_dir(project)).is_dir()
    assert (skills_dir(project)).is_dir()
    assert (tasks_dir(project)).is_dir()
    # HATS-284: ./CLAUDE.md is now a thin scaffold (~65 bytes); the aggregator
    # at .claude/CLAUDE.md carries the framework injection.
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert (project / ".agent" / "ai-hats" / "imports.md").exists()


@pytest.mark.parametrize("role", ALL_ROLES, ids=ALL_ROLES)
def test_set_all_roles(cli_project, role):
    """Every built-in role assembles without errors via CLI."""
    project, runner = cli_project

    r = runner.invoke(main, ["config", "set", "-r", role, "-p", "claude"])
    assert r.exit_code == 0, r.output
    assert "Warning" not in r.output
    assert (project / "CLAUDE.md").exists()
    assert "@./.agent/ai-hats/imports.md" in (project / "CLAUDE.md").read_text()
    assert (project / ".agent" / "ai-hats" / "imports.md").exists()


def test_status_after_set(cli_project):
    """ai-hats config status shows role and components after set."""
    project, runner = cli_project

    runner.invoke(main, ["config", "set", "-r", ALL_ROLES[0], "-p", "claude"])

    r = runner.invoke(main, ["config", "status"])
    assert r.exit_code == 0, r.output
    assert ALL_ROLES[0] in r.output


def test_bump_after_set(cli_project, monkeypatch):
    """HATS-470: `ai-hats self bump` removed from CLI. The bump operation
    is now reached via `python -m ai_hats._bump_internal` (hidden) or
    inline from `ai-hats self init` / `self update`. Exercise the
    internal entry-point directly here.
    """
    project, runner = cli_project

    runner.invoke(main, ["config", "set", "-r", ALL_ROLES[0], "-p", "claude"])

    prompt_before = (project / "CLAUDE.md").read_text()
    aggregator_before = (project / ".agent" / "ai-hats" / "imports.md").read_text()

    # `ai-hats self bump` must NOT be a registered click command anymore.
    bump_cli_attempt = runner.invoke(main, ["self", "bump"])
    assert bump_cli_attempt.exit_code != 0
    assert "No such command" in bump_cli_attempt.output

    # The hidden module entry-point still works.
    from ai_hats import _bump_internal

    monkeypatch.chdir(project)
    rc = _bump_internal.main([])
    assert rc == 0

    # ./CLAUDE.md (scaffold) is byte-stable; canonical aggregator is
    # also stable for the same role.
    prompt_after = (project / "CLAUDE.md").read_text()
    aggregator_after = (project / ".agent" / "ai-hats" / "imports.md").read_text()
    assert prompt_before == prompt_after
    assert aggregator_before == aggregator_after


def test_init_unknown_role_fails_loud(cli_project):
    """ai-hats self init-r <unknown> exits non-zero and leaves no artifacts on disk."""
    project, runner = cli_project

    result = runner.invoke(main, ["self", "init", "-p", "claude", "-r", "nonexistent-role"])

    assert result.exit_code != 0, result.output
    assert "nonexistent-role" in result.output
    assert "Available roles" in result.output
    assert "Initialized" not in result.output
    # No filesystem artifacts should have been created.
    assert not (project / "ai-hats.yaml").exists()
    assert not (project / ".agent").exists()
    assert not (project / "CLAUDE.md").exists()


def test_init_unknown_provider_fails_loud(cli_project):
    """ai-hats self init-p <unknown> exits non-zero without creating artifacts."""
    project, runner = cli_project

    result = runner.invoke(main, ["self", "init", "-p", "bogus-provider"])

    assert result.exit_code != 0, result.output
    assert "bogus-provider" in result.output
    assert "Initialized" not in result.output
    assert not (project / "ai-hats.yaml").exists()
    assert not (project / ".agent").exists()


def test_set_unknown_role_fails_loud(cli_project):
    """ai-hats config set -r <unknown> exits non-zero even when project is already initialized."""
    project, runner = cli_project

    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "claude"])
    claude_before = (project / "CLAUDE.md").read_text()

    result = runner.invoke(main, ["config", "set", "-r", "nonexistent-role"])

    assert result.exit_code != 0, result.output
    assert "nonexistent-role" in result.output
    assert "Role set" not in result.output
    # Existing composition must remain intact.
    assert (project / "CLAUDE.md").read_text() == claude_before


def test_set_unknown_provider_only_fails_loud(cli_project):
    """ai-hats config set -p <unknown> (provider-only, project already initialized) fails loud."""
    project, runner = cli_project

    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "claude"])

    from ai_hats.models import ProjectConfig

    cfg_before = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert cfg_before.provider == "claude"

    result = runner.invoke(main, ["config", "set", "-p", "bogus-provider"])

    assert result.exit_code != 0, result.output
    assert "bogus-provider" in result.output
    # Provider in ai-hats.yaml must not have been overwritten.
    cfg_after = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert cfg_after.provider == "claude"


def test_set_idempotent_via_cli(cli_project):
    """Repeated set does not break existing state."""
    project, runner = cli_project

    runner.invoke(main, ["config", "set", "-r", ALL_ROLES[0], "-p", "claude"])
    prompt_first = (project / "CLAUDE.md").read_text()

    r = runner.invoke(main, ["config", "set", "-r", ALL_ROLES[0], "-p", "claude"])
    assert r.exit_code == 0, r.output

    prompt_second = (project / "CLAUDE.md").read_text()
    assert prompt_first == prompt_second


# -- Role override (shadow prompt) CLI tests --


def _capture_launch(monkeypatch):
    """Replace _launch_session with a recorder. Returns the captured calls list.

    Used by HATS-087 tests to verify unknown top-level flags are forwarded
    to the session-launch path with the correct extra_args.
    """
    import ai_hats.cli as cli

    calls: list[dict] = []

    def _record(provider=None, role=None, extra_args=None, tags=None):
        calls.append(
            {
                "provider": provider,
                "role": role,
                "extra_args": list(extra_args or []),
                "tags": tags,
            }
        )

    monkeypatch.setattr(cli, "_launch_session", _record)
    return calls


def test_passthrough_resume_flag_forwarded_to_launch(cli_project, monkeypatch):
    """HATS-087: `ai-hats --resume <id>` forwards the flag through to _launch_session
    instead of failing with 'No such command' / 'No such option'."""
    project, runner = cli_project
    calls = _capture_launch(monkeypatch)

    result = runner.invoke(main, ["--resume", "abc123"])

    assert "No such command" not in (result.output or "")
    assert "No such option" not in (result.output or "")
    assert len(calls) == 1, (
        f"_launch_session was called {len(calls)} times, output: {result.output!r}"
    )
    assert calls[0]["extra_args"] == ["--resume", "abc123"]


def test_passthrough_provider_then_unknown_flag(cli_project, monkeypatch):
    """Known top-level flags are still consumed by click; unknown flags pass through."""
    project, runner = cli_project
    calls = _capture_launch(monkeypatch)

    result = runner.invoke(main, ["--provider", "claude", "--resume", "abc123"])

    assert "No such command" not in (result.output or "")
    assert len(calls) == 1
    assert calls[0]["provider"] == "claude"
    assert calls[0]["extra_args"] == ["--resume", "abc123"]


def test_passthrough_no_args_still_launches_session(cli_project, monkeypatch):
    """Bare `ai-hats` (no args) still routes through _launch_session with empty extras."""
    project, runner = cli_project
    calls = _capture_launch(monkeypatch)

    runner.invoke(main, [])

    assert len(calls) == 1
    assert calls[0]["extra_args"] == []


def test_passthrough_known_subcommand_still_dispatches(cli_project, monkeypatch):
    """`ai-hats config status` still routes via click subcommand dispatcher, not _launch_session."""
    project, runner = cli_project
    calls = _capture_launch(monkeypatch)  # should NOT be called

    # Use `status` (a no-side-effect read-only command); `task list` would also
    # work but requires .agent/ to be initialized.
    result = runner.invoke(main, ["config", "status"])

    # Subcommand may exit non-zero on uninitialized project — that's fine,
    # the point is _launch_session was NOT invoked.
    assert "No such command" not in (result.output or "")
    assert len(calls) == 0


def test_subcommands_work_with_passthrough_context(cli_project):
    """Subcommands like set/status still work despite ignore_unknown_options."""
    project, runner = cli_project

    r = runner.invoke(main, ["config", "set", "-r", ALL_ROLES[0], "-p", "claude"])
    assert r.exit_code == 0, r.output

    r = runner.invoke(main, ["config", "status"])
    assert r.exit_code == 0, r.output
    assert ALL_ROLES[0] in r.output


def test_override_creates_shadow_prompt_without_modifying_project(cli_project):
    """--role override produces a temp file and leaves CLAUDE.md untouched."""
    from pathlib import Path

    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig
    from ai_hats.providers import ClaudeProvider

    project, runner = cli_project

    # Init + set base role. HATS-407: CLI writes default_role, not active_role.
    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "claude"])
    original_claude = (project / "CLAUDE.md").read_text()
    original_profile = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert original_profile.default_role == "assistant"
    assert original_profile.active_role == ""  # runtime-only field

    # Build override for a different role (simulate what WrapRunner.run does)
    asm = Assembler(project)
    provider = ClaudeProvider()
    result = asm.composer.compose("sre")
    args, env, _ = provider.build_session_prompt(project, result, "test-sid")

    # Shadow prompt created
    assert args[0] == "--system-prompt-file"
    override_path = Path(args[1])
    assert override_path.exists()
    override_content = override_path.read_text()
    assert "sre" in override_content.lower() or "RELIABILITY" in override_content.upper()

    # Project files NOT modified
    assert (project / "CLAUDE.md").read_text() == original_claude
    after_profile = ProjectConfig.from_yaml(project / "ai-hats.yaml")
    assert after_profile.default_role == "assistant"
    assert after_profile.active_role == ""

    override_path.unlink()


def test_multiple_parallel_overrides_are_independent(cli_project):
    """Multiple simultaneous role overrides get independent temp files."""
    from pathlib import Path

    from ai_hats.assembler import Assembler
    from ai_hats.providers import ClaudeProvider

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "claude"])

    asm = Assembler(project)
    provider = ClaudeProvider()

    # Simulate 3 parallel override sessions for different roles. Each session
    # gets its own session_id (HATS-294 isolation contract).
    overrides = {}
    for role in ("sre", "go-dev", "architect"):
        result = asm.composer.compose(role)
        args, _, _ = provider.build_session_prompt(project, result, f"test-sid-{role}")
        override_path = Path(args[1])
        overrides[role] = {
            "path": override_path,
            "content": override_path.read_text(),
        }

    # All temp files exist simultaneously
    for role, info in overrides.items():
        assert info["path"].exists(), f"Override file for {role} missing"

    # All temp files are distinct paths
    paths = [str(info["path"]) for info in overrides.values()]
    assert len(set(paths)) == 3, "Override files must be distinct"

    # Each contains its own role content, not another role's
    assert (
        "sre" in overrides["sre"]["content"].lower()
        or "RELIABILITY" in overrides["sre"]["content"].upper()
    )
    assert "GO DEVELOPER" in overrides["go-dev"]["content"]
    assert (
        "architect" in overrides["architect"]["content"].lower()
        or "ARCHITECT" in overrides["architect"]["content"]
    )

    # HATS-294: project CLAUDE.md still imports the canonical aggregator;
    # aggregator now contains only user-rules (empty in this fixture).
    # Per-session role content lives in the override temp files asserted above.
    claude_scaffold = (project / "CLAUDE.md").read_text()
    assert "@./.agent/ai-hats/imports.md" in claude_scaffold

    # Cleanup
    for info in overrides.values():
        info["path"].unlink()


def test_gemini_override_creates_session_rules_dir(cli_project):
    """Gemini override uses GEMINI_CLI_PROJECT_RULES_PATH with isolated rules dir."""
    import shutil
    from pathlib import Path

    from ai_hats.assembler import Assembler
    from ai_hats.providers import GeminiProvider

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "gemini"])

    asm = Assembler(project)
    provider = GeminiProvider()

    # Build two parallel overrides
    result_a = asm.composer.compose("judge")
    _, env_a, _ = provider.build_session_prompt(project, result_a, "test-sid-a")
    result_b = asm.composer.compose("go-dev")
    _, env_b, _ = provider.build_session_prompt(project, result_b, "test-sid-b")

    dir_a = Path(env_a["GEMINI_CLI_PROJECT_RULES_PATH"])
    dir_b = Path(env_b["GEMINI_CLI_PROJECT_RULES_PATH"])

    # Independent dirs
    assert dir_a != dir_b
    assert dir_a.exists()
    assert dir_b.exists()

    # Each has its own mandatory role file
    assert (dir_a / "00_MANDATORY_ROLE.md").exists()
    assert (dir_b / "00_MANDATORY_ROLE.md").exists()
    assert (dir_a / "00_MANDATORY_ROLE.md").read_text() != (
        dir_b / "00_MANDATORY_ROLE.md"
    ).read_text()

    # HATS-407: `ai-hats config set` is yaml-only — ./GEMINI.md is not
    # materialized until the runtime set_role path runs (lazy bootstrap
    # on first session). The per-session rules dir captures the composed
    # role independently, so this test no longer asserts ./GEMINI.md
    # state.

    shutil.rmtree(dir_a)
    shutil.rmtree(dir_b)


# -- Update command tests --


def test_migrate_cleanup_removes_legacy_backlog_md(tmp_path):
    """Idempotent cleanup: stale backlog.md is removed; second call is a no-op.

    HATS-285: cleanup helper moved from cli/maintenance.py to Assembler.
    """
    from ai_hats.assembler import Assembler

    legacy = tmp_path / ".agent" / "backlog.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# stale content from old version\n")

    actions = Assembler._cleanup_obsolete_files(tmp_path)
    assert legacy.exists() is False
    assert any("backlog.md" in a for a in actions)

    # Idempotent — second call finds nothing.
    assert Assembler._cleanup_obsolete_files(tmp_path) == []


def test_migrate_cleanup_skips_when_already_clean(tmp_path):
    """Project without legacy files yields no cleanup actions."""
    from ai_hats.assembler import Assembler

    (tmp_path / ".agent").mkdir()
    assert Assembler._cleanup_obsolete_files(tmp_path) == []


def test_migrate_cleanup_sweeps_stale_last_backup_pointer(tmp_path):
    """HATS-407: stale `.last_backup` pointer file and its referenced
    tmp backup dir (created by the retired `Assembler._backup()` helper
    via ``tempfile.mkdtemp(prefix="ai-hats-backup-")``) are swept by
    `_cleanup_obsolete_files`.

    Symmetric closure of removing the `_backup()/_restore()` chain:
    projects upgrading from v0.6 still have orphan pointer files left by
    pre-HATS-407 `set_role` calls; bump should wipe them in one shot.
    """
    import tempfile
    from pathlib import Path

    from ai_hats.assembler import Assembler

    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    canon = tmp_path / ".agent" / "ai-hats"
    canon.mkdir(parents=True)

    # Real-world fixture: use the same prefix the retired _backup()
    # helper produced so the sweeper's safety gate
    # (basename startswith "ai-hats-backup-") matches.
    backup_payload = Path(tempfile.mkdtemp(prefix="ai-hats-backup-"))
    (backup_payload / "marker").write_text("payload")

    pointer = canon / ".last_backup"
    pointer.write_text(str(backup_payload))

    actions = Assembler._cleanup_obsolete_files(tmp_path)

    assert not pointer.exists(), "pointer file must be swept"
    assert not backup_payload.exists(), "referenced backup dir must be removed"
    assert any(".last_backup" in a for a in actions), actions

    # Idempotent on second call.
    assert Assembler._cleanup_obsolete_files(tmp_path) == []


def test_migrate_cleanup_sweeps_stale_last_backup_as_directory(tmp_path):
    """If `.last_backup` exists as a directory (legacy v3 shape pre-tracker
    migration), `_cleanup_obsolete_files` removes the directory."""
    from ai_hats.assembler import Assembler

    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    canon = tmp_path / ".agent" / "ai-hats"
    canon.mkdir(parents=True)
    backup_dir = canon / ".last_backup"
    backup_dir.mkdir()
    (backup_dir / "stale.txt").write_text("stale")

    actions = Assembler._cleanup_obsolete_files(tmp_path)
    assert not backup_dir.exists()
    assert any(".last_backup" in a for a in actions)


def test_migrate_cleanup_ignores_non_tmp_pointer_target(tmp_path):
    """A pointer that names a non-/tmp/ path must NOT cause rmtree (defense
    against corrupt pointers being able to redirect cleanup at user files).
    Pointer file itself is still removed."""
    from ai_hats.assembler import Assembler

    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    canon = tmp_path / ".agent" / "ai-hats"
    canon.mkdir(parents=True)

    user_dir = tmp_path / "user-precious"
    user_dir.mkdir()
    (user_dir / "important.txt").write_text("DO NOT DELETE")

    pointer = canon / ".last_backup"
    pointer.write_text(str(user_dir))

    Assembler._cleanup_obsolete_files(tmp_path)

    # Pointer swept; user content preserved.
    assert not pointer.exists()
    assert (user_dir / "important.txt").exists()


def test_update_command_uses_uv_reinstall():
    """Update command must use `uv pip install --reinstall` and NOT disable cache.

    HATS-763: the env engine is uv. ``--reinstall`` is uv's
    ``--force-reinstall`` (re-installs the target unconditionally); the uv
    content-addressed cache (~/.cache/uv) still serves transitive deps, so we
    never pass a cache-disabling flag. ``--python sys.executable`` is
    load-bearing (B1): a bare ``uv pip install`` would resolve the nearest
    discoverable venv, not this interpreter. This is the fail-under-revert
    canary for ``cli/maintenance.py:_build_update_cmd``.
    """
    from ai_hats.cli.maintenance import _build_update_cmd

    cmd = _build_update_cmd()
    assert cmd[0] == "uv", "must use the uv engine"
    assert cmd[:3] == ["uv", "pip", "install"]
    assert "--reinstall" in cmd, "uv caches git installs; --reinstall is required"
    assert "--no-cache-dir" not in cmd, (
        "--reinstall already re-installs the target; never disable uv's "
        "content-addressed cache for transitive deps"
    )
    assert "--no-deps" not in cmd, (
        "must NOT pass --no-deps: new deps in pyproject.toml (e.g. ptyprocess in HATS-207) "
        "would otherwise be skipped on update and crash at runtime"
    )
    assert any("git+ssh://" in arg for arg in cmd), "must install from git"
    assert "--python" in cmd and cmd[cmd.index("--python") + 1] == sys.executable, (
        "B1: must pin --python sys.executable so uv targets THIS interpreter"
    )


def test_run_self_update_fails_loud_without_uv(monkeypatch):
    """HATS-763 D2: the wizard self-update step fails loud (clean exit, not a raw
    FileNotFoundError traceback) when uv is absent from PATH."""
    from ai_hats.cli.assembly import _run_self_update

    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(SystemExit) as exc:
        _run_self_update()
    assert exc.value.code == 1


def test_update_command_runs_via_cli(cli_project, monkeypatch):
    """ai-hats self updateinvokes pip with correct flags (mocked subprocess)."""
    import subprocess

    project, runner = cli_project

    # Init project so migrate doesn't fail
    runner.invoke(main, ["config", "set", "-p", "claude"])
    _pin_edge(project)

    captured_cmds = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        # For version check subprocess, return a version string
        stdout = "0.3.0" if "__version__" in str(cmd) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(main, ["self", "update"])
    assert result.exit_code == 0, result.output
    # Rich's console.status() writes the spinner via ANSI directly to the
    # terminal — it doesn't land in CliRunner's captured output. The
    # post-install version banner + Library section are the stable
    # markers that the flow completed successfully.
    assert "Current version:" in result.output
    assert "Library:" in result.output

    # Interaction-count only: exactly one uv install call.
    # Cmd composition is covered by test_update_command_uses_uv_reinstall;
    # the real install flow is covered by tests/e2e/test_install.py.
    install_cmds = [c for c in captured_cmds if "--reinstall" in c]
    assert len(install_cmds) == 1, f"Expected 1 uv install call, got {len(install_cmds)}"


def test_update_command_reports_failure(cli_project, monkeypatch):
    """ai-hats self updateshows error when pip fails."""
    import subprocess

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    _pin_edge(project)

    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Connection refused")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(main, ["self", "update"])
    assert "Update failed" in result.output
    assert "Connection refused" in result.output
    # HATS-718: a failed install must be machine-detectable — exit non-zero so
    # scripted chains (`self update && self init`) and CI stop instead of
    # proceeding against a half-updated environment.
    assert result.exit_code == 1, \
        f"expected exit 1 on failed update, got {result.exit_code}; output:\n{result.output}"


def test_update_shows_version_transition(cli_project, monkeypatch):
    """ai-hats self updateshows old → new version when version changes."""
    import subprocess

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    _pin_edge(project)

    def mock_run(cmd, **kwargs):
        # Version check returns new version
        if "__version__" in str(cmd):
            return subprocess.CompletedProcess(cmd, 0, stdout="0.5.0\n", stderr="")
        # git clone for changelog — simulate success
        if "git" in cmd and "clone" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        # git log for changelog
        if "git" in cmd and "log" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="abc1234 feat: new feature\ndef5678 fix: bug fix\n",
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(main, ["self", "update"])
    assert result.exit_code == 0, result.output
    # Shows version transition
    assert "0.5.0" in result.output
    # Shows changelog
    assert "Recent changes" in result.output
    assert "new feature" in result.output


# -- Task create CLI tests --


def test_task_create_auto_id(cli_project):
    """ai-hats task create TITLE works without --id and defaults to TASK- prefix."""
    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "claude"])

    result = runner.invoke(main, ["task", "create", "My test task", "-d", "desc"])
    assert result.exit_code == 0, result.output
    assert "Created" in result.output
    assert "My test task" in result.output
    assert "TASK-001" in result.output
    assert (tasks_dir(project) / "TASK-001" / "task.yaml").exists()


def test_init_task_prefix_flag(cli_project):
    """ai-hats self init--task-prefix ACME persists prefix and drives task create."""
    import yaml

    project, runner = cli_project

    result = runner.invoke(main, ["self", "init", "-p", "claude", "--task-prefix", "ACME"])
    assert result.exit_code == 0, result.output
    assert "Task prefix" in result.output
    assert "ACME" in result.output

    raw = yaml.safe_load((project / "ai-hats.yaml").read_text())
    assert raw["task_prefix"] == "ACME"

    r = runner.invoke(main, ["task", "create", "First"])
    assert r.exit_code == 0, r.output
    assert "ACME-001" in r.output


def test_init_task_prefix_rejects_bad_format(cli_project):
    """ai-hats self init--task-prefix with invalid chars fails loud and does nothing."""
    project, runner = cli_project

    result = runner.invoke(main, ["self", "init", "-p", "claude", "--task-prefix", "bad prefix"])
    assert result.exit_code != 0, result.output
    assert "task_prefix" in result.output.lower() or "Invalid" in result.output
    assert not (project / "ai-hats.yaml").exists()
    assert not (project / ".agent").exists()


def test_init_task_prefix_reinit_conflict(cli_project):
    """Re-running init with a different --task-prefix fails, yaml untouched."""
    import yaml

    project, runner = cli_project

    runner.invoke(main, ["self", "init", "-p", "claude", "--task-prefix", "ACME"])
    result = runner.invoke(main, ["self", "init", "-p", "claude", "--task-prefix", "BETA"])
    assert result.exit_code != 0, result.output
    assert "conflict" in result.output.lower() or "ACME" in result.output

    raw = yaml.safe_load((project / "ai-hats.yaml").read_text())
    assert raw["task_prefix"] == "ACME"
    # Re-running with the SAME prefix is a no-op.
    r = runner.invoke(main, ["self", "init", "-p", "claude", "--task-prefix", "ACME"])
    assert r.exit_code == 0, r.output


def test_init_idempotent_on_existing_yaml(cli_project):
    """Re-init on an existing yaml is idempotent — does not modify user config."""
    project, runner = cli_project

    runner.invoke(main, ["self", "init", "-p", "claude"])

    # User customizes threshold so the feedback block is serialized in yaml.
    runner.invoke(
        main,
        ["config", "feedback", "session-retro", "smart", "--threshold", "turns=99,tool_calls=99"],
    )

    yaml_before = (project / "ai-hats.yaml").read_text()
    assert "99" in yaml_before

    # Re-init must not modify an already-existing yaml.
    r = runner.invoke(main, ["self", "init", "-p", "claude"])
    assert r.exit_code == 0, r.output

    yaml_after = (project / "ai-hats.yaml").read_text()
    assert yaml_before == yaml_after, "re-init should not modify an existing yaml"


def test_task_prefix_honored_from_yaml(cli_project):
    """Explicit task_prefix in ai-hats.yaml overrides the TASK- default."""
    import yaml

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "claude"])

    cfg_path = project / "ai-hats.yaml"
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    raw["task_prefix"] = "ACME"
    cfg_path.write_text(yaml.dump(raw))

    result = runner.invoke(main, ["task", "create", "Custom prefix"])
    assert result.exit_code == 0, result.output
    assert "ACME-001" in result.output
    assert (tasks_dir(project) / "ACME-001").exists()


def test_task_prefix_auto_detected_from_legacy_tasks(cli_project):
    """A project with pre-existing HATS-* tasks (and no task_prefix in yaml)
    keeps using HATS instead of resetting to TASK."""
    import yaml

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "claude"])

    # Simulate a legacy tasks dir and strip any task_prefix from the yaml.
    legacy_id = "HATS-042"
    (tasks_dir(project) / legacy_id).mkdir(parents=True)
    (tasks_dir(project) / legacy_id / "task.yaml").write_text(
        "id: HATS-042\ntitle: Legacy\nstate: done\npriority: low\ncreated: 2025-01-01T00:00:00Z\nupdated: 2025-01-01T00:00:00Z\n"
    )
    cfg_path = project / "ai-hats.yaml"
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    raw.pop("task_prefix", None)
    cfg_path.write_text(yaml.dump(raw))

    result = runner.invoke(main, ["task", "create", "Next legacy"])
    assert result.exit_code == 0, result.output
    assert "HATS-043" in result.output
    # Auto-detected prefix must be persisted to yaml for subsequent runs.
    raw_after = yaml.safe_load(cfg_path.read_text())
    assert raw_after.get("task_prefix") == "HATS"


def test_task_create_explicit_id(cli_project):
    """ai-hats task create TITLE --id ID uses the given ID."""
    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "claude"])

    result = runner.invoke(main, ["task", "create", "Explicit ID task", "--id", "CUSTOM-001"])
    assert result.exit_code == 0, result.output
    assert "CUSTOM-001" in result.output
    assert (tasks_dir(project) / "CUSTOM-001" / "task.yaml").exists()


def test_task_list_table_filters(cli_project):
    """ai-hats task list shows table, hides done, supports filters."""
    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-r", "assistant", "-p", "claude"])

    # Create tasks with different states and priorities
    runner.invoke(main, ["task", "create", "Active task", "-p", "high"])
    runner.invoke(main, ["task", "create", "Low task", "-p", "low"])
    runner.invoke(main, ["task", "create", "Done task", "-p", "medium"])

    # Transition third task to done (brainstorm → plan → execute → document → review → done)
    runner.invoke(main, ["task", "transition", "TASK-003", "plan"])
    # Fill every required section so the per-section plan→execute gate passes
    # (HATS-230 created the gate; HATS-635 made it per-section).
    plan_path = tasks_dir(project) / "TASK-003" / "plan.md"
    plan_path.write_text(
        "# Plan\n\n## Requirements\nfilled in for the test\n\n"
        "## Scope & Out-of-scope\nin/out\n\n"
        "## Steps\n- [x] do thing\n\n"
        "## Verification Protocol\npytest\n"
    )
    for state in ["execute", "document", "review", "done"]:
        runner.invoke(main, ["task", "transition", "TASK-003", state])

    # Default: done is hidden
    result = runner.invoke(main, ["task", "list"])
    assert result.exit_code == 0, result.output
    assert "Active task" in result.output
    assert "Low task" in result.output
    assert "Done task" not in result.output

    # --all includes done
    result = runner.invoke(main, ["task", "list", "--all"])
    assert result.exit_code == 0, result.output
    assert "Done task" in result.output

    # --priority filter
    result = runner.invoke(main, ["task", "list", "--priority", "high"])
    assert result.exit_code == 0, result.output
    assert "Active task" in result.output
    assert "Low task" not in result.output

    # --state filter
    result = runner.invoke(main, ["task", "list", "--state", "brainstorm"])
    assert result.exit_code == 0, result.output
    assert "Active task" in result.output


# ---- HATS-213 stage-2 verify + activation banner ----


def _make_mock_run_factory(
    *,
    version: str = "0.3.0",
    verify_rc: int = 0,
    pip_list_before: str = "[]",
    pip_list_after: str = "[]",
):
    """Build a subprocess.run mock that distinguishes the calls update() makes."""
    import subprocess

    state = {"pip_list_calls": 0}

    def mock_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
        if "ai_hats._bootstrap" in cmd_str and "verify" in cmd_str:
            return subprocess.CompletedProcess(
                cmd,
                verify_rc,
                stdout="",
                stderr="" if verify_rc == 0 else "missing X",
            )
        if "pip" in cmd_str and "list" in cmd_str:
            state["pip_list_calls"] += 1
            payload = pip_list_before if state["pip_list_calls"] == 1 else pip_list_after
            return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")
        if "__version__" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{version}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return mock_run


def test_t10_update_invokes_stage2_verify(cli_project, monkeypatch):
    """update() runs `python -m ai_hats._bootstrap verify` after pip install."""
    import subprocess

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    _pin_edge(project)

    captured: list[list[str]] = []

    def mock_run(cmd, **kwargs):
        captured.append(list(cmd) if isinstance(cmd, list) else [str(cmd)])
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
        if "__version__" in cmd_str:
            return subprocess.CompletedProcess(cmd, 0, stdout="0.3.0\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(main, ["self", "update"])
    assert result.exit_code == 0, result.output

    verify_calls = [
        c
        for c in captured
        if any("ai_hats._bootstrap" in str(arg) for arg in c) and any(arg == "verify" for arg in c)
    ]
    assert len(verify_calls) == 1, f"expected one stage-2 verify call, got {verify_calls}"


def test_t11_update_warns_on_stage2_failure_does_not_crash(cli_project, monkeypatch):
    """Stage-2 verify failure surfaces as a yellow warning, update keeps going."""
    import subprocess

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    _pin_edge(project)

    monkeypatch.setattr(subprocess, "run", _make_mock_run_factory(verify_rc=1))

    result = runner.invoke(main, ["self", "update"])
    assert result.exit_code == 0, result.output
    assert "Post-install verify warned" in result.output


def test_t12_update_prints_activation_banner_on_dep_change(cli_project, monkeypatch):
    """New dependency in pip list → activation banner shows it."""
    import subprocess

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    _pin_edge(project)

    before = '[{"name": "click", "version": "8.1.0"}]'
    after = '[{"name": "click", "version": "8.1.0"}, {"name": "ptyprocess", "version": "0.7.0"}]'

    monkeypatch.setattr(
        subprocess,
        "run",
        _make_mock_run_factory(pip_list_before=before, pip_list_after=after),
    )

    result = runner.invoke(main, ["self", "update"])
    assert result.exit_code == 0, result.output
    assert "Dependency activation" in result.output
    assert "ptyprocess" in result.output
    assert "self-heal" in result.output


def test_t13_update_no_banner_when_deps_unchanged(cli_project, monkeypatch):
    """Identical pip list snapshots → no activation banner."""
    import subprocess

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    _pin_edge(project)

    same = '[{"name": "click", "version": "8.1.0"}]'
    monkeypatch.setattr(
        subprocess,
        "run",
        _make_mock_run_factory(pip_list_before=same, pip_list_after=same),
    )

    result = runner.invoke(main, ["self", "update"])
    assert result.exit_code == 0, result.output
    assert "Dependency activation" not in result.output


def test_update_shows_already_up_to_date(cli_project, monkeypatch):
    """ai-hats self updateshows 'already up to date' when versions match."""
    import subprocess

    from ai_hats import __version__

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    _pin_edge(project)

    def mock_run(cmd, **kwargs):
        if "__version__" in str(cmd):
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{__version__}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = runner.invoke(main, ["self", "update"])
    assert result.exit_code == 0, result.output
    assert "Already up to date" in result.output


# ---------- HATS-764: config set --channel / config status Channel line ----------


def test_config_set_channel_edge_roundtrips(cli_project):
    import yaml

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    r = runner.invoke(main, ["config", "set", "--channel", "edge"])
    assert r.exit_code == 0, r.output
    raw = yaml.safe_load((project / "ai-hats.yaml").read_text())
    assert raw["harness"] == {"channel": "edge"}
    s = runner.invoke(main, ["config", "status"])
    assert s.exit_code == 0, s.output
    assert "Channel:" in s.output and "edge" in s.output


def test_config_set_channel_local_with_path(cli_project):
    import yaml

    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    r = runner.invoke(main, ["config", "set", "--channel", "local", "--path", "."])
    assert r.exit_code == 0, r.output
    raw = yaml.safe_load((project / "ai-hats.yaml").read_text())
    assert raw["harness"] == {"channel": "local", "path": "."}


def test_config_set_stable_is_byte_clean(cli_project):
    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    r = runner.invoke(main, ["config", "set", "--channel", "stable"])
    assert r.exit_code == 0, r.output
    # stable is the default → omitted from yaml (no spurious harness: block).
    assert "harness" not in (project / "ai-hats.yaml").read_text()


def test_config_set_repo_rejected_without_edge(cli_project):
    project, runner = cli_project
    runner.invoke(main, ["config", "set", "-p", "claude"])
    r = runner.invoke(main, ["config", "set", "--channel", "local", "--repo", "https://x/y.git"])
    assert r.exit_code == 1
    assert "--repo is only valid with --channel edge" in r.output
