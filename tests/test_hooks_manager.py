"""HATS-837: HooksManager DI seam + value-type guarantees (review follow-ups)."""

import pytest

from ai_hats.assembler import Assembler
from ai_hats.hooks_manager import (
    HookChange,
    HookChangeKind,
    HookSurface,
    HookSyncResult,
    HookSyncStatus,
)
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


def test_assembler_accepts_injected_hooks_manager(tmp_path, monkeypatch):
    """A1: HooksManager is injectable, so Assembler can be tested with a fake/mock."""
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "home"))
    ProjectConfig(provider="claude").save(tmp_path / PROJECT_CONFIG)
    sentinel = object()
    asm = Assembler(tmp_path, hooks=sentinel)
    assert asm.hooks is sentinel


def test_default_hooks_manager_is_wired_when_not_injected(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "home"))
    ProjectConfig(provider="claude").save(tmp_path / PROJECT_CONFIG)
    asm = Assembler(tmp_path)
    from ai_hats.hooks_manager import HooksManager

    assert isinstance(asm.hooks, HooksManager)


def test_materialize_writes_scripts_before_wiring(tmp_path, monkeypatch):
    """Script bytes land before the settings.json entry referencing them
    (HATS-1123).

    Wiring-first leaves a window where a managed PreToolUse command points at a
    file that does not exist yet; Claude Code execs it and every Bash call in
    the session fails with ENOENT until the next successful materialize.
    """
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "home"))
    ProjectConfig(provider="claude").save(tmp_path / PROJECT_CONFIG)
    asm = Assembler(tmp_path)
    order: list[str] = []

    real_materialize = asm.hooks.materialize_runtime_hooks
    real_provider = asm.hooks.resolve_provider(asm.project_config.provider)
    real_ensure = real_provider.ensure_runtime_hooks

    def spy_materialize(*a, **kw):
        order.append("scripts")
        return real_materialize(*a, **kw)

    def spy_ensure(*a, **kw):
        order.append("wiring")
        return real_ensure(*a, **kw)

    monkeypatch.setattr(asm.hooks, "materialize_runtime_hooks", spy_materialize)
    monkeypatch.setattr(real_provider, "ensure_runtime_hooks", spy_ensure)
    monkeypatch.setattr(asm.hooks, "resolve_provider", lambda _n: real_provider)

    asm.hooks.materialize(None)

    assert order == ["scripts", "wiring"]


def test_hookchange_coerces_strings_and_rejects_unknown():
    """H1: string values coerce to enums; an unknown value raises (the guarantee)."""
    c = HookChange("runtime", "x.sh", "content")
    assert c.surface is HookSurface.RUNTIME
    assert c.kind is HookChangeKind.CONTENT
    assert c.surface == "runtime"  # StrEnum stays string-compatible
    with pytest.raises(ValueError):
        HookChange("bogus", "x", "content")
    with pytest.raises(ValueError):
        HookChange("runtime", "x", "nope")


def test_hooksyncresult_status_coerces_and_rejects_unknown():
    r = HookSyncResult("in-sync")
    assert r.status is HookSyncStatus.IN_SYNC
    assert r.status == "in-sync"
    with pytest.raises(ValueError):
        HookSyncResult("weird")


def test_materialize_skips_when_binary_behind_source(tmp_path, monkeypatch):
    """HATS-1127: materialize() must refuse to write hooks if installed binary is behind upstream."""
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "home"))
    ProjectConfig(provider="claude").save(tmp_path / PROJECT_CONFIG)
    asm = Assembler(tmp_path)

    called = False

    def spy_materialize(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr(asm.hooks, "binary_behind_source", lambda: True)
    monkeypatch.setattr(asm.hooks, "materialize_runtime_hooks", spy_materialize)

    sink: list[str] = []
    asm.hooks.materialize(None, warnings_sink=sink)

    assert not called, "expected materialize to skip when binary_behind_source() is True"
    assert any("behind upstream" in w for w in sink)

