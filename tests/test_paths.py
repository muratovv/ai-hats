"""Tests for ai-hats path conventions (HATS-274, HATS-275, HATS-316)."""

from __future__ import annotations

import pytest

from ai_hats.paths import (
    LEGACY_PATH_MAP,
    ai_hats_dir,
    audits_dir,
    backlog_dir,
    decisions_dir,
    detect_legacy_state,
    experiments_dir,
    handoffs_dir,
    hooks_dir,
    hypotheses_dir,
    last_backup_path,
    legacy_paths_by_class,
    library_dir,
    normalize_ai_hats_dir,
    normalize_venv_path,
    pipeline_steps_dir,
    proposals_dir,
    retros_dir,
    rules_dir,
    runs_dir,
    sessions_dir,
    skills_dir,
    state_md_path,
    tasks_dir,
    tracker_dir,
    traces_dir,
    venv_path,
    worktree_state_path,
    worktrees_dir,
)


def test_ai_hats_dir_default(tmp_path, monkeypatch):
    """No env override → default <project>/.agent/ai-hats/."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / ".agent" / "ai-hats"
    assert base.is_dir()


def test_ai_hats_dir_env_override(tmp_path, monkeypatch):
    """AI_HATS_DIR overrides default. Project dir ignored."""
    custom = tmp_path / "custom-runtime"
    monkeypatch.setenv("AI_HATS_DIR", str(custom))
    base = ai_hats_dir(tmp_path / "project")
    assert base == custom
    assert base.is_dir()


def test_ai_hats_dir_env_expands_user(tmp_path, monkeypatch):
    """AI_HATS_DIR with ~ gets expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AI_HATS_DIR", "~/my-ai-hats")
    base = ai_hats_dir(tmp_path / "project")
    assert base == tmp_path / "my-ai-hats"
    assert base.is_dir()


def test_ai_hats_dir_idempotent_mkdir(tmp_path, monkeypatch):
    """Calling twice doesn't fail and returns same path."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    first = ai_hats_dir(tmp_path)
    second = ai_hats_dir(tmp_path)
    assert first == second
    assert first.is_dir()


def test_traces_dir_under_ai_hats(tmp_path, monkeypatch):
    """traces_dir is <ai_hats_dir>/traces and gets created."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    td = traces_dir(tmp_path)
    assert td == tmp_path / ".agent" / "ai-hats" / "traces"
    assert td.is_dir()


def test_traces_dir_respects_env_override(tmp_path, monkeypatch):
    """AI_HATS_DIR cascades: traces_dir lives under the override."""
    custom = tmp_path / "custom"
    monkeypatch.setenv("AI_HATS_DIR", str(custom))
    td = traces_dir(tmp_path / "project")
    assert td == custom / "traces"
    assert td.is_dir()


def test_pipeline_steps_dir_under_ai_hats(tmp_path, monkeypatch):
    """pipeline_steps_dir is <ai_hats_dir>/pipeline_steps/."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    psd = pipeline_steps_dir(tmp_path)
    assert psd == tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    assert psd.is_dir()


def test_pipeline_steps_dir_respects_env_override(tmp_path, monkeypatch):
    """AI_HATS_DIR cascades into pipeline_steps_dir resolution too."""
    custom = tmp_path / "custom"
    monkeypatch.setenv("AI_HATS_DIR", str(custom))
    psd = pipeline_steps_dir(tmp_path / "project")
    assert psd == custom / "pipeline_steps"
    assert psd.is_dir()


# ---------- HATS-316: yaml-based resolution ----------


def test_ai_hats_dir_reads_yaml(tmp_path, monkeypatch):
    """ai-hats.yaml `ai_hats_dir` field takes precedence over bootstrap default."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: custom/runtime\nprovider: claude\n"
    )
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / "custom" / "runtime"
    assert base.is_dir()


def test_ai_hats_dir_env_overrides_yaml(tmp_path, monkeypatch):
    """env AI_HATS_DIR beats yaml ai_hats_dir."""
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: from-yaml\nprovider: claude\n"
    )
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "from-env"))
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / "from-env"


def test_ai_hats_dir_falls_back_when_yaml_missing_field(tmp_path, monkeypatch):
    """yaml without ai_hats_dir field → bootstrap default (e.g. v3 yaml before migration)."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    (tmp_path / "ai-hats.yaml").write_text("schema_version: 3\nprovider: claude\n")
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / ".agent" / "ai-hats"


def test_ai_hats_dir_handles_corrupt_yaml(tmp_path, monkeypatch):
    """Malformed yaml → bootstrap default (no crash)."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    (tmp_path / "ai-hats.yaml").write_text("not: valid: yaml: [\n")
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / ".agent" / "ai-hats"


# ---------- HATS-316: sessions/ resolvers ----------


@pytest.mark.parametrize(
    "fn,subpath",
    [
        (sessions_dir, "sessions"),
        (runs_dir, "sessions/runs"),
        (retros_dir, "sessions/retros"),
        (audits_dir, "sessions/audits"),
        (handoffs_dir, "sessions/handoffs"),
        (experiments_dir, "sessions/experiments"),
        (worktrees_dir, "sessions/worktrees"),
        (worktree_state_path, "sessions/worktree.json"),
    ],
)
def test_sessions_class_resolvers(tmp_path, monkeypatch, fn, subpath):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    result = fn(tmp_path)
    assert result == tmp_path / ".agent" / "ai-hats" / subpath
    # New resolvers are pure: they don't mkdir the leaf.
    assert not result.exists() or result.is_dir() is False or fn is sessions_dir


def test_sessions_resolvers_respect_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "custom"))
    assert runs_dir(tmp_path) == tmp_path / "custom" / "sessions" / "runs"
    assert worktree_state_path(tmp_path) == tmp_path / "custom" / "sessions" / "worktree.json"


# ---------- HATS-316: tracker/ resolvers ----------


@pytest.mark.parametrize(
    "fn,subpath",
    [
        (tracker_dir, "tracker"),
        (backlog_dir, "tracker/backlog"),
        (tasks_dir, "tracker/backlog/tasks"),
        (proposals_dir, "tracker/backlog/proposals"),
        (hypotheses_dir, "tracker/hypotheses"),
        (decisions_dir, "tracker/decisions"),
        (state_md_path, "STATE.md"),
    ],
)
def test_tracker_class_resolvers(tmp_path, monkeypatch, fn, subpath):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    assert fn(tmp_path) == tmp_path / ".agent" / "ai-hats" / subpath


def test_tracker_resolvers_respect_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    (tmp_path / "ai-hats.yaml").write_text("schema_version: 4\nai_hats_dir: ah\nprovider: claude\n")
    assert tasks_dir(tmp_path) == tmp_path / "ah" / "tracker" / "backlog" / "tasks"
    assert state_md_path(tmp_path) == tmp_path / "ah" / "STATE.md"


# ---------- HATS-316: library/ resolvers ----------


@pytest.mark.parametrize(
    "fn,subpath",
    [
        (library_dir, "library"),
        (rules_dir, "library/rules"),
        (skills_dir, "library/skills"),
        (hooks_dir, "library/hooks"),
    ],
)
def test_library_class_resolvers(tmp_path, monkeypatch, fn, subpath):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    assert fn(tmp_path) == tmp_path / ".agent" / "ai-hats" / subpath


# ---------- HATS-316: framework-root ----------


def test_last_backup_path(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    assert last_backup_path(tmp_path) == tmp_path / ".agent" / "ai-hats" / ".last_backup"


# ---------- HATS-316: legacy migration helpers ----------


def _seed_legacy_layout(project_dir):
    """Create every legacy path in LEGACY_PATH_MAP so detection sees them all."""
    for legacy in LEGACY_PATH_MAP:
        target = project_dir / legacy
        target.parent.mkdir(parents=True, exist_ok=True)
        if legacy.endswith(".md") or legacy.endswith(".json") or legacy.endswith(".last_backup"):
            target.write_text("seed")
        else:
            target.mkdir(parents=True, exist_ok=True)


def test_detect_legacy_state_returns_all_pairs(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    _seed_legacy_layout(tmp_path)
    pairs = detect_legacy_state(tmp_path)
    assert len(pairs) == len(LEGACY_PATH_MAP)
    # Sanity: every old path is absolute and exists; every new path is under ai_hats_dir.
    base = tmp_path / ".agent" / "ai-hats"
    for old, new in pairs:
        assert old.is_absolute() and old.exists()
        assert str(new).startswith(str(base))


def test_detect_legacy_state_empty_project(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    assert detect_legacy_state(tmp_path) == []


def test_legacy_paths_by_class_filters(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    _seed_legacy_layout(tmp_path)
    sessions = legacy_paths_by_class(tmp_path, "sessions")
    tracker = legacy_paths_by_class(tmp_path, "tracker")
    library = legacy_paths_by_class(tmp_path, "library")
    root = legacy_paths_by_class(tmp_path, "root")
    assert len(sessions) + len(tracker) + len(library) + len(root) == len(LEGACY_PATH_MAP)
    # No overlap.
    all_olds = {p[0] for p in sessions + tracker + library + root}
    assert len(all_olds) == len(LEGACY_PATH_MAP)


# ---------- HATS-316: normalize_ai_hats_dir validation ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (".agent/ai-hats", ".agent/ai-hats"),
        (".agent/ai-hats/", ".agent/ai-hats"),
        ("custom-dir", "custom-dir"),
        ("nested/path/here", "nested/path/here"),
    ],
)
def test_normalize_ai_hats_dir_accepts(raw, expected):
    assert normalize_ai_hats_dir(raw) == expected


@pytest.mark.parametrize(
    "bad",
    ["", ".", "/", "/abs/path", "../escape", "a/../b"],
)
def test_normalize_ai_hats_dir_rejects(bad):
    with pytest.raises(ValueError):
        normalize_ai_hats_dir(bad)


# ---------- HATS-334: venv_path resolver + validation ----------


def test_venv_path_default(tmp_path, monkeypatch):
    """No env, no yaml → <ai_hats_dir>/.venv."""
    monkeypatch.delenv("AI_HATS_VENV", raising=False)
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    assert venv_path(tmp_path) == tmp_path / ".agent" / "ai-hats" / ".venv"


def test_venv_path_yaml_relative(tmp_path, monkeypatch):
    """yaml.venv_path relative → resolved against project_dir."""
    monkeypatch.delenv("AI_HATS_VENV", raising=False)
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        "venv_path: .venv\nprovider: claude\n"
    )
    assert venv_path(tmp_path) == tmp_path / ".venv"


def test_venv_path_yaml_absolute(tmp_path, monkeypatch):
    """yaml.venv_path absolute → returned as-is."""
    monkeypatch.delenv("AI_HATS_VENV", raising=False)
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    abs_target = tmp_path / "shared-venv"
    (tmp_path / "ai-hats.yaml").write_text(
        f"schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        f"venv_path: {abs_target}\nprovider: claude\n"
    )
    assert venv_path(tmp_path) == abs_target


def test_venv_path_env_overrides_yaml(tmp_path, monkeypatch):
    """AI_HATS_VENV env beats yaml.venv_path."""
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        "venv_path: .venv\nprovider: claude\n"
    )
    override = tmp_path / "env-override"
    monkeypatch.setenv("AI_HATS_VENV", str(override))
    assert venv_path(tmp_path) == override


def test_venv_path_env_expands_user(tmp_path, monkeypatch):
    """AI_HATS_VENV with ~ gets expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AI_HATS_VENV", "~/my-venv")
    assert venv_path(tmp_path / "project") == tmp_path / "my-venv"


def test_venv_path_handles_corrupt_yaml(tmp_path, monkeypatch):
    """Malformed yaml → fall through to default (no crash)."""
    monkeypatch.delenv("AI_HATS_VENV", raising=False)
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    (tmp_path / "ai-hats.yaml").write_text("not: valid: yaml: [\n")
    assert venv_path(tmp_path) == tmp_path / ".agent" / "ai-hats" / ".venv"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (".venv", ".venv"),
        ("custom/venv", "custom/venv"),
        ("/opt/myvenv", "/opt/myvenv"),
        ("/opt/myvenv/", "/opt/myvenv"),
    ],
)
def test_normalize_venv_path_accepts(raw, expected):
    """venv_path allows both relative and absolute (unlike ai_hats_dir)."""
    assert normalize_venv_path(raw) == expected


@pytest.mark.parametrize(
    "bad",
    ["", ".", "/", "../escape", "a/../b"],
)
def test_normalize_venv_path_rejects(bad):
    """venv_path rejects empty / dot / dotdot just like ai_hats_dir,
    but absolute is OK."""
    with pytest.raises(ValueError):
        normalize_venv_path(bad)


def test_normalize_venv_path_allows_absolute_unlike_ai_hats_dir():
    """Pin the deliberate divergence from normalize_ai_hats_dir."""
    assert normalize_venv_path("/opt/venv") == "/opt/venv"
    with pytest.raises(ValueError):
        normalize_ai_hats_dir("/opt/venv")
