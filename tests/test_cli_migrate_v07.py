"""CliRunner-level tests for `ai-hats self migrate-v07` (HATS-408 P3).

CliRunner exercises the click command in-process — it covers flag parsing,
exit-code translation, refusal/force decision flow, idempotency, and the
no-yaml-file early exit. The real-subprocess e2e gate test lives at
``tests/e2e/test_migrate_v07.py`` (P4) and asserts on the full git commit
envelope; this layer focuses on behaviour that doesn't need a real shell.

To avoid an expensive ``Assembler`` construction (which scans library
layers), we monkeypatch ``cli.maintenance._assembler`` and
``cli.maintenance._build_tier2_source_lookup`` with fakes that return
empty composition / lookup. Real-library coverage happens at P4.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.cli import maintenance as maint


# ----- Fixtures -----


def _git_init(project: Path) -> None:
    subprocess.run(["git", "-C", str(project), "init", "-q", "-b", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "config", "user.email", "t@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project), "config", "user.name", "Test"], check=True
    )
    subprocess.run(["git", "-C", str(project), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(project), "commit", "-q", "-m", "seed"], check=True
    )


def _git_log_count(project: Path) -> int:
    out = subprocess.run(
        ["git", "-C", str(project), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    )
    return len([line for line in out.stdout.splitlines() if line.strip()])


@pytest.fixture
def cli_project(tmp_path, monkeypatch):
    """Minimal v0.7-shaped project — no canonical artefacts, no edits to migrate."""
    project = tmp_path / "p"
    project.mkdir()
    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        "default_role: dev\n"
    )
    (project / ".agent" / "ai-hats").mkdir(parents=True)
    monkeypatch.chdir(project)
    # Stub out Assembler construction so we don't depend on a real library.
    monkeypatch.setattr(maint, "_assembler", lambda _pdir=None: _FakeAssembler(project))
    monkeypatch.setattr(maint, "_build_tier2_source_lookup", lambda _asm: {})
    return project, CliRunner()


@pytest.fixture
def v06_project(tmp_path, monkeypatch):
    """Project carrying a v0.6 canonical artefact (user-edited role.md)."""
    project = tmp_path / "p"
    project.mkdir()
    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
    )
    canonical = project / ".agent" / "ai-hats"
    canonical.mkdir(parents=True)
    (canonical / "role.md").write_text("USER EDITED ROLE CONTENT\n")
    (canonical / "user-rules").mkdir()
    _git_init(project)
    monkeypatch.chdir(project)
    monkeypatch.setattr(maint, "_assembler", lambda _pdir=None: _FakeAssembler(project))
    monkeypatch.setattr(maint, "_build_tier2_source_lookup", lambda _asm: {})
    return project, CliRunner()


class _FakeAssembler:
    """Stand-in for ai_hats.assembler.Assembler — only the surface migrate-v07 uses."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.composer = _FakeComposer()
        # project_config is read by _build_tier2_source_lookup (already stubbed
        # in the fixtures) — provide a minimal placeholder.
        from ai_hats.models import ProjectConfig

        self.project_config = ProjectConfig(
            provider="claude", ai_hats_dir=".agent/ai-hats",
            active_role="dev", default_role="dev",
        )

    def _get_overlay(self, _role):
        return None

    def write_canonical(self, _result) -> None:
        canonical = self.project_dir / ".agent" / "ai-hats"
        canonical.mkdir(parents=True, exist_ok=True)
        (canonical / "user-rules").mkdir(exist_ok=True)
        (canonical / "imports.md").write_text("")  # empty v0.7 aggregator
        (canonical / "MANAGED").write_text("imports.md\n")


class _FakeComposer:
    def __init__(self) -> None:
        self.resolver = _FakeResolver()

    def compose(self, _role, overlay=None):
        from ai_hats.composer import CompositionResult
        from ai_hats.models import HooksConfig

        return CompositionResult(
            name="dev", priorities=[], rules=[], skills=[],
            hooks=HooksConfig(), injections=[],
        )


class _FakeResolver:
    def resolve_rule_dir(self, _name):
        return None

    def resolve_skill_dir(self, _name):
        return None


# ----- No-yaml early exit -----


def test_no_yaml_file_exits_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["self", "migrate-v07"])
    assert result.exit_code == 2
    assert "no ai-hats.yaml" in result.stdout


# ----- Idempotency / no-op -----


def test_already_migrated_no_op(cli_project):
    project, runner = cli_project
    result = runner.invoke(main, ["self", "migrate-v07"])
    assert result.exit_code == 0, result.output
    assert "Already migrated" in result.stdout


# ----- Refusal path -----


def test_refuse_on_user_edit_exits_1_no_writes(v06_project):
    project, runner = v06_project
    role_md = project / ".agent" / "ai-hats" / "role.md"
    original = role_md.read_bytes()
    before_commits = _git_log_count(project)

    result = runner.invoke(main, ["self", "migrate-v07"])

    assert result.exit_code == 1
    assert "refusing" in result.stdout
    assert "role.md" in result.stdout
    assert "user-rules" in result.stdout  # guidance pointer
    # No writes.
    assert role_md.read_bytes() == original
    # No commit.
    assert _git_log_count(project) == before_commits


# ----- Force path -----


def test_force_overwrites_with_stderr_warn(v06_project):
    project, runner = v06_project
    role_md = project / ".agent" / "ai-hats" / "role.md"
    before_commits = _git_log_count(project)

    result = runner.invoke(main, ["self", "migrate-v07", "--force"])

    assert result.exit_code == 0, result.stdout + "\nSTDERR:\n" + result.stderr
    # role.md was deleted by sweep, then NOT regenerated (write_canonical only
    # writes imports.md / MANAGED in our fake) — confirms the sweep ran.
    assert not role_md.exists()
    # stderr WARN for the overwritten user edit.
    assert "overwriting" in result.stderr
    assert "role.md" in result.stderr
    # Atomic single commit was created.
    assert _git_log_count(project) == before_commits + 1


# ----- --no-commit path -----


def test_no_commit_leaves_changes_staged(v06_project):
    project, runner = v06_project
    before_commits = _git_log_count(project)

    result = runner.invoke(
        main, ["self", "migrate-v07", "--force", "--no-commit"]
    )

    assert result.exit_code == 0, result.output
    # No new commit.
    assert _git_log_count(project) == before_commits
    # But the changes are staged.
    diff = subprocess.run(
        ["git", "-C", str(project), "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=True,
    )
    staged = diff.stdout.strip().splitlines()
    assert any("role.md" in line for line in staged)
    assert "Staged" in result.stdout


# ----- --check-branches additive warning -----


def test_check_branches_warns_on_sibling_branch_modification(v06_project):
    project, runner = v06_project
    # Create a sibling branch that touches role.md.
    role_md_rel = ".agent/ai-hats/role.md"
    subprocess.run(
        ["git", "-C", str(project), "checkout", "-q", "-b", "sibling"],
        check=True,
    )
    (project / role_md_rel).write_text("sibling edit\n")
    subprocess.run(
        ["git", "-C", str(project), "commit", "-aq", "-m", "wip"], check=True
    )
    subprocess.run(
        ["git", "-C", str(project), "checkout", "-q", "main"], check=True
    )

    result = runner.invoke(
        main, ["self", "migrate-v07", "--check-branches"]
    )

    # Still refuses (the seed user-edit hasn't gone away) — but the warning
    # rides along, additive on top of refusal.
    assert result.exit_code == 1
    assert "sibling" in result.stdout
    assert role_md_rel in result.stdout
    assert "lost" in result.stdout  # "edits will be lost"


# ----- yaml hardening persists on force -----


def test_force_persists_yaml_hardening(v06_project):
    project, runner = v06_project
    # v06_project fixture only has active_role: dev (no default_role) —
    # from_yaml heals it in memory; migrate-v07 must persist on success.
    result = runner.invoke(main, ["self", "migrate-v07", "--force"])
    assert result.exit_code == 0, result.output

    import yaml

    saved = yaml.safe_load((project / "ai-hats.yaml").read_text())
    assert saved["default_role"] == "dev"


# ----- Idempotent rerun under --force -----


def test_idempotent_rerun_under_force(v06_project):
    project, runner = v06_project
    first = runner.invoke(main, ["self", "migrate-v07", "--force"])
    assert first.exit_code == 0
    commits_after_first = _git_log_count(project)

    second = runner.invoke(main, ["self", "migrate-v07", "--force"])

    assert second.exit_code == 0
    assert _git_log_count(project) == commits_after_first  # no second commit
    assert (
        "Already migrated" in second.stdout
        or "nothing to commit" in second.stdout
    )
