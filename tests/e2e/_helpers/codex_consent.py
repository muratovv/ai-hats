"""Disposable Codex session on the production plan: surface half, consent layer, env."""

from __future__ import annotations

import os
from pathlib import Path

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.surfaces.codex.provider import CodexSurface
from ai_hats.surfaces.plan import MaterializationPlan
from ai_hats_core.layout import ProjectLayout
from tests._plan_helpers import composition_of

from .env import clean_env
from .git import git
from .sessions import build_session, stand_in_session
from .surfaces import write_surface_shims


def planned_session(
    tmp_path: Path, monkeypatch
) -> tuple[Path, dict[str, str], MaterializationPlan]:
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "-b", "master")
    git(project, "config", "user.email", "test@example.com")  # ai-hats: allow-secret
    git(project, "config", "user.name", "Test")
    (project / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    git(project, "add", ".gitignore")
    git(project, "commit", "-m", "init")
    ProjectConfig(provider="codex", active_role="assistant").save(project / PROJECT_CONFIG)
    # The surface resolves a session home under it and refuses a base home that
    # is not an existing directory, so it has to be on disk before the plan.
    codex_base = tmp_path / "codex-base"
    codex_base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_base))
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "user-home"))
    library = Path(__file__).resolve().parents[3] / "packages/ai-hats-library/src/ai_hats_library"
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(library))
    assembler = Assembler(project)
    assembler.init()
    composition = composition_of(
        assembler.composer.compose("assistant"),
        layout=ProjectLayout.at(project),
        resolver=assembler.resolver,
    )
    env = clean_env()
    env["AI_HATS_USER_HOME"] = str(tmp_path / "user-home")
    env["PATH"] = os.pathsep.join((str(write_surface_shims(tmp_path / "bin")), os.defpath))
    stand_in_session(env, project, "consent-test", provider="codex")
    env["AI_HATS_DIR"] = str(project / ".agent" / "ai-hats")
    # One plan, surface half and consent layer together, the way the runner does.
    plan = build_session(
        project, composition, CodexSurface(), "consent-test", environ=env, middleware=True
    )
    env.update(plan.env)
    return project, env, plan


def session(tmp_path: Path, monkeypatch) -> tuple[Path, dict[str, str]]:
    project, env, _plan = planned_session(tmp_path, monkeypatch)
    return project, env
