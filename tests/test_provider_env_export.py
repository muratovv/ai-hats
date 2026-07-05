"""HATS-819 — ClaudeProvider.get_env exports ``AI_HATS_DIR`` to runtime hooks.

A materialized runtime hook must not derive a WRITE path from ``__file__`` depth
(the secret-guard incident: a telemetry ``.log`` landed in the committed skills
source tree). The engine hands every hook a clean writable anchor by exporting
``AI_HATS_DIR`` into the launched provider process env (``wrap_runner`` merges
``provider.get_env`` into the ``claude`` subprocess env, which hook subprocesses
inherit). This is the generic engine half of the fix — the hook still self-
protects when ``AI_HATS_DIR`` is absent (direct ``claude`` launch).

Fail-under-revert: drop the ``AI_HATS_DIR`` key from ``ClaudeProvider.get_env``
and ``test_claude_get_env_exports_ai_hats_dir`` goes RED.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.paths import ai_hats_dir
from ai_hats.providers import ClaudeProvider
from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR


def test_claude_get_env_exports_ai_hats_dir(tmp_path: Path) -> None:
    env = ClaudeProvider().get_env(tmp_path / "session", tmp_path)
    assert env[ENV_AI_HATS_DIR] == str(ai_hats_dir(tmp_path))


def test_claude_get_env_ai_hats_dir_defaults_under_project(tmp_path: Path) -> None:
    # No ambient override (conftest scrubs AI_HATS_DIR) → bootstrap default.
    env = ClaudeProvider().get_env(tmp_path / "session", tmp_path)
    assert env[ENV_AI_HATS_DIR] == str(tmp_path / ".agent" / "ai-hats")


def test_claude_get_env_ai_hats_dir_honours_env_override(
    tmp_path: Path, monkeypatch
) -> None:
    # ai_hats_dir() gives the AI_HATS_DIR env var precedence — get_env must
    # surface the resolved override, not the in-project default.
    override = tmp_path / "shared-ai-hats"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(override))
    env = ClaudeProvider().get_env(tmp_path / "session", tmp_path)
    assert env[ENV_AI_HATS_DIR] == str(override)


def test_claude_get_env_exports_project_dir_pair(tmp_path: Path) -> None:
    # HATS-897: the pin carries its scope — the resolver drops the pair when
    # it leaks into another project's shell.
    env = ClaudeProvider().get_env(tmp_path / "session", tmp_path)
    assert env[AI_HATS_PROJECT_DIR_ENV] == str(tmp_path)


def test_claude_get_env_self_heals_foreign_pair(tmp_path: Path, monkeypatch) -> None:
    # HATS-897: a leaked foreign pair must not be re-pinned into children —
    # get_env resolves the project's OWN base and pins a fresh pair.
    foreign_root = tmp_path / "other-repo"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(foreign_root / ".agent" / "ai-hats"))
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(foreign_root))
    project = tmp_path / "project"
    project.mkdir()
    with pytest.warns(UserWarning, match=ENV_AI_HATS_DIR):
        env = ClaudeProvider().get_env(tmp_path / "session", project)
    assert env[ENV_AI_HATS_DIR] == str(project / ".agent" / "ai-hats")
    assert env[AI_HATS_PROJECT_DIR_ENV] == str(project)


def test_ai_hats_dir_survives_wrap_runner_env_merge(tmp_path: Path) -> None:
    # Mirrors wrap_runner's env build: provider.get_env layered last among the
    # ai-hats keys, so a stale ambient value is overridden by the resolved path.
    provider = ClaudeProvider()
    base = {ENV_AI_HATS_DIR: "/stale/leak", "PATH": "/usr/bin"}
    merged = {**base, **provider.get_env(tmp_path / "session", tmp_path)}
    assert merged[ENV_AI_HATS_DIR] == str(ai_hats_dir(tmp_path))
