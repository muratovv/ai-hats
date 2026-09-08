"""Disposable Codex session with production hook and wrapper materialization."""

from __future__ import annotations

import os
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.consent_wrapper import materialize_consent_wrappers
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats.surfaces.codex.provider import CodexSurface

from .env import clean_env
from .git import git
from .sessions import stand_in_session
from .surfaces import write_surface_shims


def session(tmp_path: Path, monkeypatch, artifacts=None) -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "-b", "master")
    git(project, "config", "user.email", "test@example.com")  # ai-hats: allow-secret
    git(project, "config", "user.name", "Test")
    (project / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    git(project, "add", ".gitignore")
    git(project, "commit", "-m", "init")
    ProjectConfig(provider="codex", active_role="assistant").save(project / PROJECT_CONFIG)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-base"))
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "user-home"))
    library = Path(__file__).resolve().parents[3] / "packages/ai-hats-library/src/ai_hats_library"
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(library))
    assembler = Assembler(project)
    assembler.init()
    result = assembler.composer.compose("assistant")
    surface = CodexSurface()
    artifacts = artifacts if artifacts is not None else BuiltArtifacts()
    surface.build_session_artifacts(
        project, result, "consent-test", run_mode=RunMode.HITL, artifacts=artifacts
    )
    env = clean_env()
    env["AI_HATS_USER_HOME"] = str(tmp_path / "user-home")
    env["PATH"] = os.pathsep.join((str(write_surface_shims(tmp_path / "bin")), os.defpath))
    stand_in_session(env, project, "consent-test", provider="codex")
    env["AI_HATS_DIR"] = str(project / ".agent" / "ai-hats")
    materialize_consent_wrappers(project, result, "consent-test", surface, artifacts, environ=env)
    env.update(artifacts.extra_env)
    return project, env
