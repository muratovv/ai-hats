"""Unit tests for migration_healer (HATS-397).

Covers the in-process behaviour of ``heal_external_refs`` and its helpers:
scan, JSON rewrite (A1), text rewrite gated by git-clean (A2), inventory
fallback (B), idempotency, and skip-list correctness.

E2E coverage (real ``ai-hats self bump`` subprocess) lives in
``tests/e2e/test_self_update_heals_legacy_refs.py``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


from ai_hats.migration_healer import (
    LegacyRef,
    _LEGACY_RE,
    heal_external_refs,
    heal_json_file,
    heal_text_file,
    is_file_git_clean,
    scan_external_refs,
    write_inventory,
)


# ---------- Helpers ----------


def _init_project(tmp_path: Path) -> Path:
    """Seed a project root with minimal ai-hats.yaml so paths.py resolves cleanly."""
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: claude\nai_hats_dir: .agent/ai-hats\n"
    )
    return tmp_path


def _init_git_repo(project_dir: Path) -> None:
    """Initialize a git repo so ``is_file_git_clean`` has something to compare."""
    subprocess.run(["git", "init", "-q"], cwd=str(project_dir), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(project_dir), check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(project_dir), check=True,
    )


def _commit_all(project_dir: Path, msg: str = "seed") -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(project_dir), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", msg],
        cwd=str(project_dir), check=True,
    )


# ---------- Regex compilation ----------


def test_legacy_regex_matches_hooks_path() -> None:
    assert _LEGACY_RE.search(".agent/hooks/foo.py") is not None


def test_legacy_regex_matches_backlog_path() -> None:
    assert _LEGACY_RE.search(".agent/backlog/tasks/X-1/plan.md") is not None


def test_legacy_regex_matches_retrospectives_path() -> None:
    assert _LEGACY_RE.search(".agent/retrospectives/2026-01-01-foo.md") is not None


def test_legacy_regex_matches_state_md() -> None:
    assert _LEGACY_RE.search(".agent/STATE.md") is not None


def test_legacy_regex_matches_gitlog() -> None:
    assert _LEGACY_RE.search(".gitlog/pipeline_runs/x") is not None


def test_legacy_regex_does_not_match_new_form() -> None:
    """The new-layout path must not be matched (idempotency guarantee)."""
    assert _LEGACY_RE.search(".agent/ai-hats/library/hooks/foo.py") is None
    assert _LEGACY_RE.search(".agent/ai-hats/tracker/backlog/tasks/X/plan.md") is None
    assert _LEGACY_RE.search(".agent/ai-hats/sessions/retros/foo.md") is None


def test_legacy_regex_does_not_match_unrelated_paths() -> None:
    assert _LEGACY_RE.search(".agent/ai-hats/foo") is None
    assert _LEGACY_RE.search("agent/hooks/foo") is None  # missing leading dot
    assert _LEGACY_RE.search(".agentX/hooks/foo") is None


# ---------- Scanning ----------


def test_scan_finds_ref_in_settings_json(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    settings = p / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"command": "$CLAUDE_PROJECT_DIR/.agent/hooks/guard.py"}]
        }]}
    }, indent=2))
    refs = scan_external_refs(p)
    assert len(refs) == 1
    assert refs[0].file == settings
    assert refs[0].legacy_substr == ".agent/hooks/"
    assert refs[0].new_substr.endswith("library/hooks/")


def test_scan_finds_ref_in_markdown(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    (p / "CLAUDE.md").write_text(
        "Hook lives at `.agent/hooks/foo.py` and is documented.\n"
    )
    refs = scan_external_refs(p)
    assert len(refs) == 1
    assert refs[0].line == 1
    assert refs[0].legacy_substr == ".agent/hooks/"


def test_scan_skips_managed_namespace(tmp_path: Path) -> None:
    """Anything under <ai_hats_dir>/ is the managed namespace; do not scan."""
    p = _init_project(tmp_path)
    inside_managed = p / ".agent" / "ai-hats" / "sessions" / "retros" / "old.md"
    inside_managed.parent.mkdir(parents=True)
    inside_managed.write_text("retro mentions `.agent/hooks/foo.py` historically\n")
    refs = scan_external_refs(p)
    assert refs == []


def test_scan_skips_git_node_modules_venv(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    for noise_dir in (".git", "node_modules", ".venv", "__pycache__"):
        f = p / noise_dir / "x.md"
        f.parent.mkdir(parents=True)
        f.write_text("`.agent/hooks/x`\n")
    refs = scan_external_refs(p)
    assert refs == []


def test_scan_skips_backup_dirs(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    backup = p / ".agent" / "backlog.bak.20260422-150614" / "x.md"
    backup.parent.mkdir(parents=True)
    backup.write_text("`.agent/hooks/x`\n")
    refs = scan_external_refs(p)
    assert refs == []


def test_scan_skips_changelog_md(tmp_path: Path) -> None:
    """CHANGELOG.md is by convention historical record — must NOT be auto-healed.

    Regression guard for HATS-416: three consecutive ``ai-hats self bump``
    runs rewrote the HATS-412 CHANGELOG entry that DESCRIBED the legacy-path
    bug, turning "canonical X instead of legacy Y" into "canonical X instead
    of legacy X" — loss of meaning. The fix excludes CHANGELOG.md from
    `_walk_candidate_files`. Counter-test confirms a sibling .md file with
    the same legacy substring IS still healed (skip is filename-specific,
    not content-specific).
    """
    p = _init_project(tmp_path)
    (p / "CHANGELOG.md").write_text(
        "## [0.6.0]\n- HATS-412 — fix references to `.agent/hooks/` legacy path.\n"
    )
    (p / "OTHER.md").write_text(
        "Hook lives at `.agent/hooks/foo.py` and is documented.\n"
    )
    refs = scan_external_refs(p)
    ref_files = {r.file.name for r in refs}
    assert "CHANGELOG.md" not in ref_files, "CHANGELOG.md must be skipped"
    assert "OTHER.md" in ref_files, "non-CHANGELOG .md files must still scan"


def test_scan_picks_up_multiple_extensions(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    (p / "a.md").write_text("`.agent/hooks/a`\n")
    (p / "b.sh").write_text("source .agent/hooks/b\n")
    (p / "c.j2").write_text("# .agent/skills/c\n")
    (p / ".envrc").write_text("PATH=.agent/hooks/d:$PATH\n")
    refs = scan_external_refs(p)
    assert {r.file.name for r in refs} == {"a.md", "b.sh", "c.j2", ".envrc"}


def test_scan_ignores_unknown_extensions(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    (p / "noisy.bin").write_bytes(b".agent/hooks/x\n")
    (p / "code.py").write_text("# .agent/hooks/x\n")  # python intentionally not scanned
    refs = scan_external_refs(p)
    assert refs == []


# ---------- Stage A1 — JSON heal ----------


def test_heal_json_rewrites_hook_path(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    settings = p / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    payload = {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"command": "$CLAUDE_PROJECT_DIR/.agent/hooks/guard.py"}]
        }]}
    }
    settings.write_text(json.dumps(payload, indent=2))
    count = heal_json_file(settings, p)
    assert count == 1
    new_data = json.loads(settings.read_text())
    cmd = new_data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert ".agent/hooks/" not in cmd
    assert "library/hooks/guard.py" in cmd


def test_heal_json_idempotent(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    settings = p / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"x": ".agent/hooks/y.py"}, indent=2))
    assert heal_json_file(settings, p) == 1
    assert heal_json_file(settings, p) == 0


def test_heal_json_preserves_trailing_newline(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    settings = p / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"x": ".agent/hooks/y"}, indent=2) + "\n"
    settings.write_text(original)
    heal_json_file(settings, p)
    assert settings.read_text().endswith("\n")


def test_heal_json_no_match_returns_zero(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    settings = p / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"x": "clean/path"}, indent=2))
    assert heal_json_file(settings, p) == 0


# ---------- Stage A2 — text heal ----------


def test_heal_text_rewrites_markdown(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    f = p / "CLAUDE.md"
    f.write_text("see `.agent/hooks/foo.py` for details\n")
    count = heal_text_file(f, p)
    assert count == 1
    assert ".agent/hooks/" not in f.read_text()
    assert "library/hooks/foo.py" in f.read_text()


def test_heal_text_idempotent(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    f = p / "CLAUDE.md"
    f.write_text("see `.agent/hooks/foo.py` for details\n")
    heal_text_file(f, p)
    assert heal_text_file(f, p) == 0


# ---------- git-clean gate ----------


def test_git_clean_returns_true_for_committed_file(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    _init_git_repo(p)
    (p / "x.md").write_text("hello\n")
    _commit_all(p)
    assert is_file_git_clean(p / "x.md", p) is True


def test_git_clean_returns_false_for_modified_file(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    _init_git_repo(p)
    (p / "x.md").write_text("hello\n")
    _commit_all(p)
    (p / "x.md").write_text("hello\nmore\n")
    assert is_file_git_clean(p / "x.md", p) is False


def test_git_clean_permissive_outside_repo(tmp_path: Path) -> None:
    """No git repo → assume clean (don't block heal on non-git projects)."""
    p = _init_project(tmp_path)
    (p / "x.md").write_text("hello\n")
    assert is_file_git_clean(p / "x.md", p) is True


# ---------- Inventory — Stage B ----------


def test_write_inventory_creates_file_with_entries(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    refs = [
        LegacyRef(
            file=p / "CLAUDE.md", line=42,
            legacy_substr=".agent/hooks/", new_substr=".agent/ai-hats/library/hooks/",
        ),
        LegacyRef(
            file=p / "docs" / "x.md", line=3,
            legacy_substr=".agent/backlog/", new_substr=".agent/ai-hats/tracker/backlog/",
        ),
    ]
    out = write_inventory(p, refs)
    assert out is not None
    assert out.exists()
    content = out.read_text()
    assert "CLAUDE.md" in content
    assert "docs/x.md" in content
    assert "L42" in content
    assert ".agent/hooks/" in content


def test_write_inventory_empty_returns_none(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    assert write_inventory(p, []) is None


# ---------- Orchestration ----------


def test_heal_external_refs_clean_project_noop(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    report = heal_external_refs(p, verbose=False)
    assert report.total == 0


def test_heal_external_refs_full_clean_git_tree(tmp_path: Path) -> None:
    """End-to-end orchestration: JSON heal + text heal under clean git tree."""
    p = _init_project(tmp_path)
    _init_git_repo(p)

    settings = p / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"cmd": ".agent/hooks/g.py"}, indent=2))

    (p / "CLAUDE.md").write_text("see `.agent/hooks/g.py`\n")
    (p / "docs.md").write_text("retro at `.agent/retrospectives/r.md`\n")

    _commit_all(p)

    report = heal_external_refs(p, verbose=False)
    assert len(report.healed_json) == 1
    assert len(report.healed_text) == 2
    assert report.inventoried == []
    assert report.inventory_path is None

    # Verify content was actually rewritten
    assert ".agent/hooks/" not in settings.read_text()
    assert ".agent/hooks/" not in (p / "CLAUDE.md").read_text()
    assert ".agent/retrospectives/" not in (p / "docs.md").read_text()


def test_heal_external_refs_dirty_file_falls_to_inventory(tmp_path: Path) -> None:
    """Dirty markdown → not auto-rewritten, lands in inventory."""
    p = _init_project(tmp_path)
    _init_git_repo(p)

    (p / "CLAUDE.md").write_text("clean\n")
    _commit_all(p)
    # Modify after commit to make it dirty
    (p / "CLAUDE.md").write_text("see `.agent/hooks/g.py`\n")

    report = heal_external_refs(p, verbose=False)
    assert report.healed_text == []
    assert len(report.inventoried) == 1
    assert report.inventory_path is not None
    assert report.inventory_path.exists()
    # Source untouched
    assert ".agent/hooks/g.py" in (p / "CLAUDE.md").read_text()


def test_heal_external_refs_json_heals_even_when_other_files_dirty(tmp_path: Path) -> None:
    """JSON A1 is always-on and doesn't depend on git state."""
    p = _init_project(tmp_path)
    _init_git_repo(p)

    settings = p / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"x": "clean"}, indent=2))
    (p / "CLAUDE.md").write_text("clean\n")
    _commit_all(p)
    # Modify both — markdown dirty, json dirty
    settings.write_text(json.dumps({"x": ".agent/hooks/g.py"}, indent=2))
    (p / "CLAUDE.md").write_text("see `.agent/hooks/g.py`\n")

    report = heal_external_refs(p, verbose=False)
    # JSON heals (A1 always-on, no git gate)
    assert len(report.healed_json) == 1
    # Markdown lands in inventory (dirty)
    assert len(report.inventoried) == 1


def test_heal_external_refs_idempotent(tmp_path: Path) -> None:
    p = _init_project(tmp_path)
    _init_git_repo(p)
    settings = p / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"cmd": ".agent/hooks/g.py"}, indent=2))
    (p / "CLAUDE.md").write_text("see `.agent/hooks/g.py`\n")
    _commit_all(p)

    heal_external_refs(p, verbose=False)
    # Re-commit the healed content so git considers it clean again
    _commit_all(p, msg="post-heal")
    report = heal_external_refs(p, verbose=False)
    assert report.total == 0
