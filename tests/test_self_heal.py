"""Unit tests for surface-plugin self-heal (HATS-966).

Pure-logic coverage: detection (find_spec over provider entry points), the
module->canonical map from packages/surfaces/*, and the heal control flow with
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
    surface_editable_map,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ai_hats_cline:ClineProvider", "ai_hats_cline"),
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
        EntryPoint(name="cline", value="ai_hats_cline_gone_966:Y",
                   group=self_heal.PROVIDER_ENTRY_POINT_GROUP),
    ]
    monkeypatch.setattr(self_heal, "_provider_entry_points", lambda: eps)
    broken = find_broken_surface_providers()
    assert [b.ep_name for b in broken] == ["cline"]
    assert broken[0].module == "ai_hats_cline_gone_966"


def test_surface_editable_map_keys_on_module(tmp_path) -> None:
    member = tmp_path / "packages" / "surfaces" / "cline"
    (member / "src" / "ai_hats_cline").mkdir(parents=True)
    (member / "src" / "ai_hats_cline" / "__init__.py").write_text("")
    mapping = surface_editable_map(tmp_path)
    assert mapping == {"ai_hats_cline": member}


def test_surface_editable_map_empty_when_no_surfaces(tmp_path) -> None:
    assert surface_editable_map(tmp_path) == {}


def _bp(module: str = "ai_hats_cline") -> BrokenProvider:
    return BrokenProvider(ep_name="cline", module=module)


def test_heal_repoints_mapped_and_verifies(tmp_path) -> None:
    canonical = tmp_path / "packages" / "surfaces" / "cline"
    calls: list = []
    result = heal_surface_editables(
        tmp_path,
        broken=[_bp()],
        mapping={"ai_hats_cline": canonical},
        installer=lambda p: calls.append(p),
        verifier=lambda m: True,
    )
    assert calls == [canonical]
    assert [h.provider.module for h in result.healed] == ["ai_hats_cline"]
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
    assert "no packages/surfaces" in result.warned[0].reason


def test_heal_warns_when_installer_raises(tmp_path) -> None:
    canonical = tmp_path / "packages" / "surfaces" / "cline"

    def boom(_p):
        raise RuntimeError("uv exploded")

    result = heal_surface_editables(
        tmp_path, broken=[_bp()], mapping={"ai_hats_cline": canonical},
        installer=boom, verifier=lambda m: True,
    )
    assert result.healed == []
    assert "re-point failed" in result.warned[0].reason


def test_heal_warns_when_still_unimportable_after_repoint(tmp_path) -> None:
    canonical = tmp_path / "packages" / "surfaces" / "cline"
    result = heal_surface_editables(
        tmp_path, broken=[_bp()], mapping={"ai_hats_cline": canonical},
        installer=lambda p: None, verifier=lambda m: False,
    )
    assert result.healed == []
    assert "still unimportable" in result.warned[0].reason


def test_heal_noop_when_nothing_broken(tmp_path) -> None:
    result = heal_surface_editables(
        tmp_path, broken=[], mapping={}, installer=lambda p: None, verifier=lambda m: True,
    )
    assert result.is_noop()
