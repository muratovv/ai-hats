"""Unit tests for the stale-editable self-heal (HATS-966).

Pure-logic coverage: detection (find_spec over provider entry points), the
module->canonical map from packages/*, and the heal control flow with
installer/verifier injected (no real ``uv`` / venv).
"""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest

from ai_hats import self_heal
from ai_hats.self_heal import (
    BrokenProvider,
    _ep_module,
    _module_resolves,
    find_broken_surface_providers,
    heal_surface_editables,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ai_hats_cline:ClineSurface", "ai_hats_cline"),
        ("pkg.sub.mod:Obj", "pkg"),
        ("  spaced :X", "spaced"),
    ],
)
def test_ep_module_extracts_top_level(value: str, expected: str) -> None:
    assert _ep_module(value) == expected


def test_module_resolves_true_for_stdlib_false_for_bogus() -> None:
    assert _module_resolves("sys") is True
    assert _module_resolves("totally_bogus_module_xyz_966") is False


def test_find_broken_surface_providers_flags_only_unresolvable(monkeypatch) -> None:
    eps = [
        EntryPoint(name="ok", value="sys:X", group=self_heal.PROVIDER_ENTRY_POINT_GROUP),
        EntryPoint(
            name="cline",
            value="ai_hats_cline_gone_966:Y",
            group=self_heal.PROVIDER_ENTRY_POINT_GROUP,
        ),
    ]
    monkeypatch.setattr(self_heal, "_provider_entry_points", lambda: eps)
    broken = find_broken_surface_providers()
    assert [b.ep_name for b in broken] == ["cline"]
    assert broken[0].module == "ai_hats_cline_gone_966"


def _bp(module: str = "ai_hats_wt") -> BrokenProvider:
    return BrokenProvider(ep_name="ai-hats-wt", module=module)


def test_heal_repoints_mapped_and_verifies(tmp_path) -> None:
    canonical = tmp_path / "packages" / "ai-hats-wt"
    calls: list = []
    result = heal_surface_editables(
        tmp_path,
        broken=[_bp()],
        mapping={"ai_hats_wt": canonical},
        installer=lambda p: calls.append(p),
        verifier=lambda m: True,
    )
    assert calls == [canonical]
    assert [h.provider.module for h in result.healed] == ["ai_hats_wt"]
    assert result.warned == []


def test_heal_warns_unmapped_and_never_installs(tmp_path) -> None:
    calls: list = []
    result = heal_surface_editables(
        tmp_path,
        broken=[_bp("some_out_of_tree_plugin")],
        mapping={},
        installer=lambda p: calls.append(p),
        verifier=lambda m: True,
    )
    assert calls == []  # never touch an unmapped package
    assert result.healed == []
    assert len(result.warned) == 1
    assert "no packages/* member" in result.warned[0].reason


def test_heal_warns_when_installer_raises(tmp_path) -> None:
    canonical = tmp_path / "packages" / "ai-hats-wt"

    def boom(_p):
        raise RuntimeError("uv exploded")

    result = heal_surface_editables(
        tmp_path,
        broken=[_bp()],
        mapping={"ai_hats_wt": canonical},
        installer=boom,
        verifier=lambda m: True,
    )
    assert result.healed == []
    assert "re-point failed" in result.warned[0].reason


def test_heal_warns_when_still_unimportable_after_repoint(tmp_path) -> None:
    canonical = tmp_path / "packages" / "ai-hats-wt"
    result = heal_surface_editables(
        tmp_path,
        broken=[_bp()],
        mapping={"ai_hats_wt": canonical},
        installer=lambda p: None,
        verifier=lambda m: False,
    )
    assert result.healed == []
    assert "still unimportable" in result.warned[0].reason


def test_heal_noop_when_nothing_broken(tmp_path) -> None:
    result = heal_surface_editables(
        tmp_path,
        broken=[],
        mapping={},
        installer=lambda p: None,
        verifier=lambda m: True,
    )
    assert result.is_noop()


# ---- run_editable_heal orchestration (repo-root resolve + fast-path + lock) ----


def _with_packages(tmp_path):
    (tmp_path / "packages").mkdir(parents=True)
    return tmp_path


def test_run_editable_heal_noop_when_not_editable(monkeypatch) -> None:
    monkeypatch.setattr("ai_hats.paths.editable_install_root", lambda name="ai-hats": None)
    assert self_heal.run_editable_heal() is None


def test_run_editable_heal_noop_when_no_packages_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(self_heal, "find_broken_surface_providers", lambda *a, **kw: [_bp()])
    assert self_heal.run_editable_heal(tmp_path, lock_path=tmp_path / "l.lock") is None


def test_run_editable_heal_noop_when_nothing_broken(monkeypatch, tmp_path) -> None:
    _with_packages(tmp_path)
    monkeypatch.setattr(self_heal, "find_broken_surface_providers", lambda *a, **kw: [])
    assert self_heal.run_editable_heal(tmp_path, lock_path=tmp_path / "l.lock") is None


def test_run_editable_heal_heals_under_lock(monkeypatch, tmp_path) -> None:
    _with_packages(tmp_path)
    monkeypatch.setattr(self_heal, "find_broken_surface_providers", lambda *a, **kw: [_bp()])
    sentinel = self_heal.HealResult(healed=[], warned=[])
    seen: dict = {}

    def fake_heal(root, **kw):
        seen["root"] = root
        return sentinel

    monkeypatch.setattr(self_heal, "heal_surface_editables", fake_heal)
    result = self_heal.run_editable_heal(tmp_path, lock_path=tmp_path / "l.lock")
    assert result is sentinel
    assert seen["root"] == tmp_path


def test_editable_install_root_none_for_unknown_dist() -> None:
    from ai_hats.paths import editable_install_root

    assert editable_install_root("no-such-dist-hats966") is None


# ---- workspace members under packages/* (HATS-1367 §3) ----


def _workspace_member(root, name: str, module: str):
    member = root / "packages" / name
    (member / "src" / module).mkdir(parents=True)
    (member / "src" / module / "__init__.py").write_text("")
    return member


def test_workspace_editable_map_keys_on_module(tmp_path) -> None:
    """A dangling workspace .pth is keyed by module, never by dist name."""
    member = _workspace_member(tmp_path, "ai-hats-wt", "ai_hats_wt")

    assert self_heal.workspace_editable_map(tmp_path) == {"ai_hats_wt": member}


def test_workspace_editable_map_empty_when_no_packages_dir(tmp_path) -> None:
    assert self_heal.workspace_editable_map(tmp_path) == {}


def test_find_broken_editables_reports_an_unresolvable_workspace_member(tmp_path, monkeypatch):
    """The launcher's heal channel was blind to workspace members entirely."""
    _workspace_member(tmp_path, "ai-hats-wt", "ai_hats_wt_gone_1367")
    monkeypatch.setattr(self_heal, "_provider_entry_points", lambda: [])

    broken = self_heal.find_broken_editables(tmp_path)

    assert [b.module for b in broken] == ["ai_hats_wt_gone_1367"]


def test_find_broken_editables_ignores_a_resolvable_workspace_member(tmp_path, monkeypatch):
    """An installed member must not be reinstalled on every launch."""
    _workspace_member(tmp_path, "ai-hats-sys", "sys")
    monkeypatch.setattr(self_heal, "_provider_entry_points", lambda: [])

    assert self_heal.find_broken_editables(tmp_path) == []


def test_run_editable_heal_repoints_a_workspace_member(tmp_path, monkeypatch) -> None:
    """End of the orchestration: a broken workspace member reaches the heal with its dir."""
    member = _workspace_member(tmp_path, "ai-hats-wt", "ai_hats_wt_gone_1367")
    monkeypatch.setattr(self_heal, "_provider_entry_points", lambda: [])
    seen: dict = {}

    def fake_heal(root, **kw):
        seen.update(kw)
        return self_heal.HealResult(healed=[], warned=[])

    monkeypatch.setattr(self_heal, "heal_surface_editables", fake_heal)
    self_heal.run_editable_heal(tmp_path, lock_path=tmp_path / "l.lock")

    assert [b.module for b in seen["broken"]] == ["ai_hats_wt_gone_1367"]
    assert seen["mapping"]["ai_hats_wt_gone_1367"] == member
