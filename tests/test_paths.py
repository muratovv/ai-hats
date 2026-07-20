"""Tests for ai-hats path conventions (HATS-274, HATS-275, HATS-316)."""

from __future__ import annotations

import pytest

from ai_hats.paths import (
    LEGACY_PATH_MAP,
    ai_hats_dir,
    audits_dir,
    backlog_dir,
    complete_sentinel,
    current_pointer,
    decisions_dir,
    ensure_ai_hats_dir,
    handoffs_dir,
    hooks_dir,
    hypotheses_dir,
    hypotheses_flat_dir,
    is_complete,
    is_usable_version,
    last_backup_path,
    legacy_paths_by_class,
    library_dir,
    normalize_ai_hats_dir,
    normalize_venv_path,
    NotAnAiHatsProjectError,
    pipeline_steps_dir,
    proposals_dir,
    read_current_sha,
    retros_dir,
    rules_dir,
    runs_dir,
    sessions_dir,
    skills_dir,
    state_md_path,
    tasks_dir,
    tracker_dir,
    traces_dir,
    user_home,
    venv_path,
    version_dir,
    versions_root,
    worktrees_dir,
)
from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR, ENV_AI_HATS_VENV, PROJECT_CONFIG


def test_ai_hats_dir_default(tmp_path, monkeypatch):
    """No env override → default <project>/.agent/ai-hats/."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / ".agent" / "ai-hats"
    assert not base.exists()  # HATS-839: pure resolution, nothing created


def test_ai_hats_dir_env_override(tmp_path, monkeypatch):
    """AI_HATS_DIR overrides default. Project dir ignored."""
    custom = tmp_path / "custom-runtime"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(custom))
    base = ai_hats_dir(tmp_path / "project")
    assert base == custom
    assert not base.exists()


def test_ai_hats_dir_env_expands_user(tmp_path, monkeypatch):
    """AI_HATS_DIR with ~ gets expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ENV_AI_HATS_DIR, "~/my-ai-hats")
    base = ai_hats_dir(tmp_path / "project")
    assert base == tmp_path / "my-ai-hats"
    assert not base.exists()


def test_ai_hats_dir_foreign_pair_ignored(tmp_path, monkeypatch):
    """HATS-897: a pin pair leaked from ANOTHER project's session is ignored."""
    foreign_root = tmp_path / "other-repo"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(foreign_root / ".agent" / "ai-hats"))
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(foreign_root))
    project = tmp_path / "project"
    project.mkdir()
    with pytest.warns(UserWarning, match=ENV_AI_HATS_DIR):
        base = ai_hats_dir(project)
    assert base == project / ".agent" / "ai-hats"


def test_ai_hats_dir_matching_pair_honored(tmp_path, monkeypatch):
    """HATS-897: the session's own pin wins even via a symlinked spelling."""
    project = tmp_path / "project"
    project.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project)
    custom = tmp_path / "custom-runtime"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(custom))
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(link))
    assert ai_hats_dir(project) == custom


def test_ai_hats_dir_pair_var_alone_is_noop(tmp_path, monkeypatch):
    """HATS-897: AI_HATS_PROJECT_DIR without AI_HATS_DIR changes nothing."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(tmp_path / "other-repo"))
    assert ai_hats_dir(tmp_path) == tmp_path / ".agent" / "ai-hats"


def test_ensure_ai_hats_dir_foreign_pair_is_not_optin(tmp_path, monkeypatch):
    """HATS-897: a leaked pair must not validate write ops in a stray dir."""
    foreign_root = tmp_path / "other-repo"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(foreign_root / ".agent" / "ai-hats"))
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(foreign_root))
    stray = tmp_path / "stray"
    stray.mkdir()
    with pytest.warns(UserWarning, match=ENV_AI_HATS_DIR):
        with pytest.raises(NotAnAiHatsProjectError):
            ensure_ai_hats_dir(stray)


def test_ai_hats_dir_pure_resolution_creates_nothing(tmp_path, monkeypatch):
    """HATS-839: ai_hats_dir resolves a stable path but never creates it."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    first = ai_hats_dir(tmp_path)
    second = ai_hats_dir(tmp_path)
    assert first == second
    assert not first.exists()


def test_traces_dir_under_ai_hats(tmp_path, monkeypatch):
    """traces_dir is <ai_hats_dir>/traces — pure resolution, not created (HATS-839)."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    td = traces_dir(tmp_path)
    assert td == tmp_path / ".agent" / "ai-hats" / "traces"
    assert not td.exists()


def test_traces_dir_respects_env_override(tmp_path, monkeypatch):
    """AI_HATS_DIR cascades: traces_dir lives under the override."""
    custom = tmp_path / "custom"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(custom))
    td = traces_dir(tmp_path / "project")
    assert td == custom / "traces"
    assert not td.exists()


def test_pipeline_steps_dir_under_ai_hats(tmp_path, monkeypatch):
    """pipeline_steps_dir is <ai_hats_dir>/pipeline_steps/."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    psd = pipeline_steps_dir(tmp_path)
    assert psd == tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    assert not psd.exists()


def test_pipeline_steps_dir_respects_env_override(tmp_path, monkeypatch):
    """AI_HATS_DIR cascades into pipeline_steps_dir resolution too."""
    custom = tmp_path / "custom"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(custom))
    psd = pipeline_steps_dir(tmp_path / "project")
    assert psd == custom / "pipeline_steps"
    assert not psd.exists()


# ---------- HATS-839: ensure_ai_hats_dir (validating creator) ----------


def test_ensure_ai_hats_dir_raises_for_non_project(tmp_path, monkeypatch):
    """A bare dir (no marker, no env) is not an ai-hats project → refuse, create nothing."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    with pytest.raises(NotAnAiHatsProjectError):
        ensure_ai_hats_dir(tmp_path)
    assert not (tmp_path / ".agent").exists()


def test_ensure_ai_hats_dir_creates_with_yaml_marker(tmp_path, monkeypatch):
    """`ai-hats.yaml` marks an onboarded project → create + return the base."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    (tmp_path / PROJECT_CONFIG).write_text("schema_version: 4\nprovider: claude\n")
    base = ensure_ai_hats_dir(tmp_path)
    assert base == tmp_path / ".agent" / "ai-hats"
    assert base.is_dir()


def test_ensure_ai_hats_dir_creates_with_agent_marker(tmp_path, monkeypatch):
    """A pre-existing `.agent/` marks an onboarded project → create + return the base."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    (tmp_path / ".agent").mkdir()
    base = ensure_ai_hats_dir(tmp_path)
    assert base == tmp_path / ".agent" / "ai-hats"
    assert base.is_dir()


def test_ensure_ai_hats_dir_creates_with_env_optin(tmp_path, monkeypatch):
    """`AI_HATS_DIR` is an explicit opt-in → create + return the override path."""
    custom = tmp_path / "runtime"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(custom))
    base = ensure_ai_hats_dir(tmp_path / "project")
    assert base == custom
    assert base.is_dir()


# ---------- HATS-316: yaml-based resolution ----------


def test_ai_hats_dir_reads_yaml(tmp_path, monkeypatch):
    """ai-hats.yaml `ai_hats_dir` field takes precedence over bootstrap default."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: custom/runtime\nprovider: claude\n"
    )
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / "custom" / "runtime"
    assert not base.exists()


def test_ai_hats_dir_env_overrides_yaml(tmp_path, monkeypatch):
    """env AI_HATS_DIR beats yaml ai_hats_dir."""
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: from-yaml\nprovider: claude\n"
    )
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(tmp_path / "from-env"))
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / "from-env"


def test_ai_hats_dir_falls_back_when_yaml_missing_field(tmp_path, monkeypatch):
    """yaml without ai_hats_dir field → bootstrap default (e.g. v3 yaml before migration)."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    (tmp_path / PROJECT_CONFIG).write_text("schema_version: 3\nprovider: claude\n")
    base = ai_hats_dir(tmp_path)
    assert base == tmp_path / ".agent" / "ai-hats"


def test_ai_hats_dir_handles_corrupt_yaml(tmp_path, monkeypatch):
    """Malformed yaml → bootstrap default (no crash)."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    (tmp_path / PROJECT_CONFIG).write_text("not: valid: yaml: [\n")
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
        (worktrees_dir, "sessions/worktrees"),
    ],
)
def test_sessions_class_resolvers(tmp_path, monkeypatch, fn, subpath):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    result = fn(tmp_path)
    assert result == tmp_path / ".agent" / "ai-hats" / subpath
    # New resolvers are pure: they don't mkdir the leaf.
    assert not result.exists() or result.is_dir() is False or fn is sessions_dir


def test_sessions_resolvers_respect_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(tmp_path / "custom"))
    assert runs_dir(tmp_path) == tmp_path / "custom" / "sessions" / "runs"


# ---------- HATS-316: tracker/ resolvers ----------


@pytest.mark.parametrize(
    "fn,subpath",
    [
        (tracker_dir, "tracker"),
        (backlog_dir, "tracker/backlog"),
        (tasks_dir, "tracker/backlog/tasks"),
        (proposals_dir, "tracker/backlog/proposals"),
        (hypotheses_dir, "tracker/backlog/hypotheses"),
        (hypotheses_flat_dir, "tracker/hypotheses"),
        (decisions_dir, "tracker/decisions"),
        (state_md_path, "STATE.md"),
    ],
)
def test_tracker_class_resolvers(tmp_path, monkeypatch, fn, subpath):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    assert fn(tmp_path) == tmp_path / ".agent" / "ai-hats" / subpath


def test_tracker_resolvers_respect_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    (tmp_path / PROJECT_CONFIG).write_text("schema_version: 4\nai_hats_dir: ah\nprovider: claude\n")
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
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    assert fn(tmp_path) == tmp_path / ".agent" / "ai-hats" / subpath


# ---------- HATS-316: framework-root ----------


def test_last_backup_path(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
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


def test_legacy_paths_by_class_filters(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_legacy_layout(tmp_path)
    sessions = legacy_paths_by_class(tmp_path, "sessions")
    tracker = legacy_paths_by_class(tmp_path, "tracker")
    library = legacy_paths_by_class(tmp_path, "library")
    root = legacy_paths_by_class(tmp_path, "root")
    all_pairs = sessions + tracker + library + root
    # Union across classes reproduces the full map, with no overlap.
    assert len(all_pairs) == len(LEGACY_PATH_MAP)
    all_olds = {p[0] for p in all_pairs}
    assert len(all_olds) == len(LEGACY_PATH_MAP)
    # Sanity: every old path is absolute and exists; every new path is under ai_hats_dir.
    base = tmp_path / ".agent" / "ai-hats"
    for old, new in all_pairs:
        assert old.is_absolute() and old.exists()
        assert str(new).startswith(str(base))


def test_legacy_paths_by_class_empty_project(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    for class_ in ("sessions", "tracker", "library", "root"):
        assert legacy_paths_by_class(tmp_path, class_) == []


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
    monkeypatch.delenv(ENV_AI_HATS_VENV, raising=False)
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    assert venv_path(tmp_path) == tmp_path / ".agent" / "ai-hats" / ".venv"


def test_venv_path_yaml_relative(tmp_path, monkeypatch):
    """yaml.venv_path relative → resolved against project_dir."""
    monkeypatch.delenv(ENV_AI_HATS_VENV, raising=False)
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nvenv_path: .venv\nprovider: claude\n"
    )
    assert venv_path(tmp_path) == tmp_path / ".venv"


def test_venv_path_yaml_absolute(tmp_path, monkeypatch):
    """yaml.venv_path absolute → returned as-is."""
    monkeypatch.delenv(ENV_AI_HATS_VENV, raising=False)
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    abs_target = tmp_path / "shared-venv"
    (tmp_path / PROJECT_CONFIG).write_text(
        f"schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        f"venv_path: {abs_target}\nprovider: claude\n"
    )
    assert venv_path(tmp_path) == abs_target


def test_venv_path_env_overrides_yaml(tmp_path, monkeypatch):
    """AI_HATS_VENV env beats yaml.venv_path."""
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nvenv_path: .venv\nprovider: claude\n"
    )
    override = tmp_path / "env-override"
    monkeypatch.setenv(ENV_AI_HATS_VENV, str(override))
    assert venv_path(tmp_path) == override


def test_venv_path_env_expands_user(tmp_path, monkeypatch):
    """AI_HATS_VENV with ~ gets expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ENV_AI_HATS_VENV, "~/my-venv")
    assert venv_path(tmp_path / "project") == tmp_path / "my-venv"


def test_venv_path_handles_corrupt_yaml(tmp_path, monkeypatch):
    """Malformed yaml → fall through to default (no crash)."""
    monkeypatch.delenv(ENV_AI_HATS_VENV, raising=False)
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    (tmp_path / PROJECT_CONFIG).write_text("not: valid: yaml: [\n")
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


# ---------- user_home (HATS-532) ----------


def test_user_home_default(monkeypatch):
    """Env unset → falls through to ``Path.home()``."""
    from pathlib import Path

    monkeypatch.delenv("AI_HATS_USER_HOME", raising=False)
    assert user_home() == Path.home()


def test_user_home_env_override(tmp_path, monkeypatch):
    """AI_HATS_USER_HOME points the resolver at an isolated dir.

    Sanity for HATS-532's primary motivation: e2e tests can isolate
    ``~/.ai-hats/`` without touching ``HOME`` (which breaks claude
    auth on macOS).
    """
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path))
    assert user_home() == tmp_path


def test_user_home_env_expands_user(tmp_path, monkeypatch):
    """AI_HATS_USER_HOME with leading ``~`` gets expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AI_HATS_USER_HOME", "~/fake-home")
    assert user_home() == tmp_path / "fake-home"


def test_user_home_env_empty_string_falls_back(monkeypatch):
    """Empty ``AI_HATS_USER_HOME`` is treated as unset."""
    from pathlib import Path

    monkeypatch.setenv("AI_HATS_USER_HOME", "")
    assert user_home() == Path.home()


# ---------- HATS-647: versioned install layout + lazy-migration resolve ----------


def _seed_version(
    project_dir,
    sha,
    *,
    make_dir=True,
    complete=True,
    pointer=True,
    sentinel=None,
    python=None,
):
    """Seed versions/<sha>/ (a fake venv) and/or versions/current.

    HATS-790 (Alt 5): the ``[project.scripts] ai-hats`` console script was
    removed, so a managed venv no longer materialises ``bin/ai-hats``; the only
    on-disk runnable marker is the interpreter ``bin/python``. Usability is now
    ``.complete`` sentinel + ``bin/python`` (HATS-648/657).

    ``complete`` is the "fully-installed venv" shorthand: it seeds BOTH the
    ``bin/python`` interpreter and the ``.complete`` sentinel. ``python`` and
    ``sentinel`` override each axis independently (default to ``complete`` when
    ``None``), so a test can seed crash residue (``bin/python`` but no
    ``.complete``), a corrupted-after-complete venv (``.complete`` but no
    ``bin/python``), or a python-broken venv (``.complete`` but ``bin/python``
    gone, HATS-657). read_current_sha requires BOTH (HATS-648/657).
    """
    root = project_dir / ".agent" / "ai-hats" / "versions"
    root.mkdir(parents=True, exist_ok=True)
    write_sentinel = complete if sentinel is None else sentinel
    write_python = complete if python is None else python
    if make_dir:
        vdir = root / sha
        vdir.mkdir(parents=True, exist_ok=True)
        if write_python:
            (vdir / "bin").mkdir(parents=True, exist_ok=True)
            (vdir / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        if write_sentinel:
            (vdir / ".complete").write_text("", encoding="utf-8")
    if pointer:
        (root / "current").write_text(f"{sha}\n", encoding="utf-8")
    return root


def test_versions_layout_paths(tmp_path, monkeypatch):
    """versions_root / version_dir / current_pointer compose under ai_hats_dir."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    base = tmp_path / ".agent" / "ai-hats" / "versions"
    assert versions_root(tmp_path) == base
    assert version_dir(tmp_path, "abc123") == base / "abc123"
    assert current_pointer(tmp_path) == base / "current"


def test_read_current_sha_present(tmp_path, monkeypatch):
    """Pointer present + versions/<sha>/ dir present → returns the sha."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_version(tmp_path, "deadbeef")
    assert read_current_sha(tmp_path) == "deadbeef"


def test_read_current_sha_missing_pointer(tmp_path, monkeypatch):
    """No pointer at all → None (legacy install, not yet versioned)."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    assert read_current_sha(tmp_path) is None


def test_read_current_sha_dangling(tmp_path, monkeypatch):
    """Pointer present but versions/<sha>/ dir absent → None (dangling)."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_version(tmp_path, "deadbeef", make_dir=False)
    assert read_current_sha(tmp_path) is None


def test_read_current_sha_corrupt_venv(tmp_path, monkeypatch):
    """Complete (sentinel present) but venv later broken (no bin/python) → None,
    so callers degrade to the self-healing legacy .venv. Corruption guard
    (HATS-647 review-finding #1), distinct from the incompleteness gate. HATS-790:
    runnability now keys on bin/python (the launcher execs `python -m ai_hats`),
    not the removed bin/ai-hats console script."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_version(tmp_path, "deadbeef", complete=False, sentinel=True)
    assert read_current_sha(tmp_path) is None


def test_read_current_sha_no_sentinel(tmp_path, monkeypatch):
    """HATS-648: bin/python present but no .complete sentinel (install killed
    mid-pip) → None. The sentinel is the completeness authority; an incomplete
    install is never resolved as current."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_version(tmp_path, "deadbeef", complete=True, sentinel=False)
    assert read_current_sha(tmp_path) is None


def test_read_current_sha_broken_python(tmp_path, monkeypatch):
    """HATS-657: complete (sentinel present) but bin/python gone (a host python
    upgrade dangles the interpreter symlink) → None. The venv is complete but NOT
    runnable, so self update must NOT see already_current and must rebuild it; the
    HATS-655 dormancy advisory must not false-fire."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_version(tmp_path, "deadbeef", complete=True, sentinel=True, python=False)
    assert read_current_sha(tmp_path) is None


def test_is_usable_version_requires_python(tmp_path, monkeypatch):
    """HATS-657 / HATS-790: is_usable_version is True only when sentinel AND
    bin/python are present — stronger than is_complete (sentinel only). The old
    bin/ai-hats clause was dropped with the console script (HATS-790)."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    # Fully usable venv.
    _seed_version(tmp_path, "deadbeef", complete=True, sentinel=True, pointer=False)
    assert is_complete(tmp_path, "deadbeef") is True
    assert is_usable_version(tmp_path, "deadbeef") is True
    # Drop only bin/python → complete but not usable.
    (version_dir(tmp_path, "deadbeef") / "bin" / "python").unlink()
    assert is_complete(tmp_path, "deadbeef") is True
    assert is_usable_version(tmp_path, "deadbeef") is False


def test_is_complete_gates_on_sentinel(tmp_path, monkeypatch):
    """is_complete is True iff the .complete sentinel is present — independent
    of bin/python (HATS-648)."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_version(tmp_path, "deadbeef", complete=True, sentinel=False, pointer=False)
    assert is_complete(tmp_path, "deadbeef") is False
    assert (
        complete_sentinel(tmp_path, "deadbeef") == version_dir(tmp_path, "deadbeef") / ".complete"
    )
    complete_sentinel(tmp_path, "deadbeef").write_text("", encoding="utf-8")
    assert is_complete(tmp_path, "deadbeef") is True


@pytest.mark.parametrize("corrupt", ["", "  ", "..", "a/b", "../escape", "x\ny"])
def test_read_current_sha_corrupt(tmp_path, monkeypatch, corrupt):
    """Empty / dotdot / path-separator pointer content → None (never escapes)."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    root = tmp_path / ".agent" / "ai-hats" / "versions"
    root.mkdir(parents=True, exist_ok=True)
    (root / "current").write_text(corrupt, encoding="utf-8")
    assert read_current_sha(tmp_path) is None


def test_venv_path_resolves_versioned(tmp_path, monkeypatch):
    """No env/yaml override + valid versions/current → versions/<sha>/."""
    monkeypatch.delenv(ENV_AI_HATS_VENV, raising=False)
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_version(tmp_path, "cafef00d")
    assert venv_path(tmp_path) == version_dir(tmp_path, "cafef00d")


def test_venv_path_dangling_pointer_falls_back_to_legacy(tmp_path, monkeypatch):
    """Dangling versions/current → legacy .venv (lazy migration keeps working)."""
    monkeypatch.delenv(ENV_AI_HATS_VENV, raising=False)
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_version(tmp_path, "deadbeef", make_dir=False)
    assert venv_path(tmp_path) == tmp_path / ".agent" / "ai-hats" / ".venv"


def test_venv_path_env_override_beats_versions(tmp_path, monkeypatch):
    """Explicit AI_HATS_VENV wins over a valid versions/current (HATS-339 override)."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    _seed_version(tmp_path, "cafef00d")
    override = tmp_path / "user-owned-venv"
    monkeypatch.setenv(ENV_AI_HATS_VENV, str(override))
    assert venv_path(tmp_path) == override


# HATS-1006: user-global Claude settings resolution


def test_claude_user_settings_json_defaults_to_home(monkeypatch, tmp_path):
    from ai_hats.paths.claude import claude_user_settings_json

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("ai_hats.paths.claude.Path.home", lambda: tmp_path)
    assert claude_user_settings_json() == tmp_path / ".claude" / "settings.json"


def test_claude_user_settings_json_honors_claude_config_dir(monkeypatch, tmp_path):
    from ai_hats.paths.claude import claude_user_settings_json

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert claude_user_settings_json() == tmp_path / "cfg" / "settings.json"
