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
