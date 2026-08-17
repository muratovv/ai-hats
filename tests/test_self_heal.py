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
        tmp_path,
        broken=[_bp()],
        mapping={"ai_hats_cline": canonical},
        installer=boom,
        verifier=lambda m: True,
    )
    assert result.healed == []
    assert "re-point failed" in result.warned[0].reason


def test_heal_warns_when_still_unimportable_after_repoint(tmp_path) -> None:
    canonical = tmp_path / "packages" / "surfaces" / "cline"
    result = heal_surface_editables(
        tmp_path,
        broken=[_bp()],
        mapping={"ai_hats_cline": canonical},
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


def _with_surfaces(tmp_path):
    (tmp_path / "packages" / "surfaces").mkdir(parents=True)
    return tmp_path


def test_run_editable_heal_noop_when_not_editable(monkeypatch) -> None:
    monkeypatch.setattr("ai_hats.paths.editable_install_root", lambda name="ai-hats": None)
    assert self_heal.run_editable_heal() is None


def test_run_editable_heal_noop_when_no_surfaces_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(self_heal, "find_broken_surface_providers", lambda *a, **kw: [_bp()])
    assert self_heal.run_editable_heal(tmp_path, lock_path=tmp_path / "l.lock") is None


def test_run_editable_heal_noop_when_nothing_broken(monkeypatch, tmp_path) -> None:
    _with_surfaces(tmp_path)
    monkeypatch.setattr(self_heal, "find_broken_surface_providers", lambda *a, **kw: [])
    assert self_heal.run_editable_heal(tmp_path, lock_path=tmp_path / "l.lock") is None


def test_run_editable_heal_heals_under_lock(monkeypatch, tmp_path) -> None:
    _with_surfaces(tmp_path)
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


# ---- workspace members, not just surfaces (HATS-1367 §3) ----


def _workspace_member(root, name: str, module: str):
    member = root / "packages" / name
    (member / "src" / module).mkdir(parents=True)
    (member / "src" / module / "__init__.py").write_text("")
    return member


def test_workspace_editable_map_keys_on_module(tmp_path) -> None:
    """A dangling workspace .pth is re-pointable the same way a surface one is."""
    member = _workspace_member(tmp_path, "ai-hats-wt", "ai_hats_wt")

    assert self_heal.workspace_editable_map(tmp_path) == {"ai_hats_wt": member}


def test_workspace_editable_map_skips_the_surfaces_category(tmp_path) -> None:
    """packages/surfaces is a category dir, not a member — surfaces have their own map."""
    _workspace_member(tmp_path, "surfaces/cline", "ai_hats_cline")

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


def test_find_uninstalled_surface_members(tmp_path, monkeypatch) -> None:
    surfaces = tmp_path / "packages" / "surfaces"
    (surfaces / "agy" / "src" / "ai_hats_agy").mkdir(parents=True)
    (surfaces / "agy" / "src" / "ai_hats_agy" / "__init__.py").write_text("")
    (surfaces / "cline" / "src" / "ai_hats_cline").mkdir(parents=True)
    (surfaces / "cline" / "src" / "ai_hats_cline" / "__init__.py").write_text("")

    # Only 'cline' is registered
    eps = [
        EntryPoint(
            name="cline", value="ai_hats_cline:Provider", group=self_heal.PROVIDER_ENTRY_POINT_GROUP
        )
    ]
    monkeypatch.setattr(self_heal, "_provider_entry_points", lambda: eps)
    # mock _module_resolves so 'ai_hats_cline' resolves and 'ai_hats_agy' does not
    monkeypatch.setattr(self_heal, "_module_resolves", lambda m: m == "ai_hats_cline")

    missing = self_heal.find_uninstalled_surface_members(tmp_path)
    assert len(missing) == 1
    assert missing[0].ep_name == "agy"
    assert missing[0].module == "ai_hats_agy"

    broken = find_broken_surface_providers(repo_root=tmp_path)
    assert [b.ep_name for b in broken] == ["agy"]


def test_get_surface_remediation(tmp_path) -> None:
    surfaces = tmp_path / "packages" / "surfaces"
    (surfaces / "agy").mkdir(parents=True)

    rem_in_tree = self_heal.get_surface_remediation("agy", repo_root=tmp_path)
    assert rem_in_tree == "uv pip install -e packages/surfaces/agy"

    rem_known = self_heal.get_surface_remediation("cline")
    assert "packages/surfaces/cline" in rem_known

    rem_codex = self_heal.get_surface_remediation("codex")
    assert rem_codex is not None
    assert "packages/surfaces/codex" in rem_codex or "ai-hats-codex" in rem_codex

    rem_claude = self_heal.get_surface_remediation("claude")
    assert rem_claude is not None
    assert "ai-hats" in rem_claude

    rem_unknown = self_heal.get_surface_remediation("unknown_provider_foo")
    assert rem_unknown is None


def test_surfaces_registry() -> None:
    from ai_hats.surfaces_registry import get_surface_info

    info_claude = get_surface_info("claude")
    assert info_claude is not None
    assert info_claude.package_name == "ai-hats"

    info_agy = get_surface_info("agy")
    assert info_agy is not None
    assert info_agy.package_name == "ai-hats-agy"

    info_codex = get_surface_info("codex")
    assert info_codex is not None
    assert info_codex.package_name == "ai-hats-codex"


def test_ensure_surface_plugin_installed_already_installed() -> None:
    from ai_hats.self_heal import ensure_surface_plugin_installed

    called = False

    def fake_installer(pkg: str) -> None:
        nonlocal called
        called = True

    assert ensure_surface_plugin_installed("claude", installer=fake_installer) is True
    assert called is False


def test_ensure_surface_plugin_installed_refreshes_targeted_heal(tmp_path) -> None:
    from ai_hats.self_heal import ensure_surface_plugin_installed

    installed = False
    installer_calls = []
    refresh_calls = []
    canonical = tmp_path / "packages" / "surfaces" / "codex"

    def fake_refresh(path) -> None:
        nonlocal installed
        refresh_calls.append(path)
        installed = True

    def fake_healer(repo_root=None):
        return self_heal.HealResult(
            healed=[
                self_heal.Healed(
                    BrokenProvider(ep_name="codex", module="ai_hats_codex"),
                    canonical,
                )
            ],
            warned=[],
        )

    result = ensure_surface_plugin_installed(
        "codex",
        installer=installer_calls.append,
        healer=fake_healer,
        installed_checker=lambda name: installed,
        refresher=fake_refresh,
    )

    assert result is True
    assert refresh_calls == [canonical]
    assert installer_calls == []


def test_ensure_surface_plugin_installed_does_not_refresh_sibling_heal(tmp_path) -> None:
    from ai_hats.self_heal import ensure_surface_plugin_installed

    installed = False
    installer_calls = []
    refresh_calls = []

    def fake_installer(package_name: str) -> None:
        nonlocal installed
        installer_calls.append(package_name)
        installed = True

    def fake_healer(repo_root=None):
        return self_heal.HealResult(
            healed=[
                self_heal.Healed(
                    BrokenProvider(ep_name="agy", module="ai_hats_agy"),
                    tmp_path / "packages" / "surfaces" / "agy",
                )
            ],
            warned=[],
        )

    result = ensure_surface_plugin_installed(
        "codex",
        installer=fake_installer,
        healer=fake_healer,
        installed_checker=lambda name: installed,
        refresher=refresh_calls.append,
    )

    assert result is True
    assert refresh_calls == []
    assert installer_calls == ["ai-hats-codex"]


@pytest.mark.parametrize(
    ("provider_name", "package_name"),
    [("cline", "ai-hats-cline"), ("codex", "ai-hats-codex")],
)
def test_ensure_surface_plugin_installed_triggers_installer(
    monkeypatch, provider_name: str, package_name: str
) -> None:
    from ai_hats.self_heal import ensure_surface_plugin_installed

    installed_state = {provider_name: False}
    installed_pkg = []

    def fake_is_installed(provider_name: str) -> bool:
        return installed_state.get(provider_name, False)

    def fake_installer(pkg: str) -> None:
        installed_pkg.append(pkg)
        installed_state[provider_name] = True

    monkeypatch.setattr("ai_hats.self_heal._is_surface_module_installed", fake_is_installed)
    monkeypatch.setattr("ai_hats.self_heal.run_editable_heal", lambda repo_root=None: None)

    res = ensure_surface_plugin_installed(provider_name, installer=fake_installer)
    assert res is True
    assert installed_pkg == [package_name]


def test_ensure_surface_plugin_installed_raises_on_installer_error(monkeypatch) -> None:
    from ai_hats.self_heal import ProviderInstallationError, ensure_surface_plugin_installed

    def fake_installer(pkg: str) -> None:
        raise RuntimeError("pip download error")

    monkeypatch.setattr("ai_hats.self_heal._is_surface_module_installed", lambda name: False)
    monkeypatch.setattr("ai_hats.self_heal.run_editable_heal", lambda repo_root=None: None)

    with pytest.raises(
        ProviderInstallationError, match="Failed to auto-install surface plugin 'cline'"
    ):
        ensure_surface_plugin_installed("cline", installer=fake_installer)


def test_ensure_surface_plugin_installed_preserves_installer_stderr() -> None:
    from subprocess import CalledProcessError

    from ai_hats.self_heal import ProviderInstallationError, ensure_surface_plugin_installed

    def fake_installer(pkg: str) -> None:
        raise CalledProcessError(97, ["uv", "pip", "install", pkg], stderr="registry denied")

    with pytest.raises(ProviderInstallationError, match="registry denied"):
        ensure_surface_plugin_installed(
            "cline",
            installer=fake_installer,
            healer=lambda repo_root=None: None,
            installed_checker=lambda name: False,
        )
