"""Tests for the provider open-registry (HATS-870 / T10).

The closed ``PROVIDERS`` dict became an open registry: providers self-register
at import and third parties register via ``register_provider`` (or the
``ai_hats.providers`` entry-point group — see ``test_provider_entry_points``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_hats import providers as prov
from ai_hats.provider_entry_points import (
    PROVIDER_ENTRY_POINT_GROUP,
    _is_first_party_entry_point,
)
from ai_hats.providers import (
    Provider,
    get_provider,
    provider_names,
    register_provider,
)


class _FakeProvider(Provider):
    @property
    def name(self) -> str:
        return "fake"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return project_dir / "FAKE.md"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result) -> str:  # noqa: ANN001
        return "fake-prompt"

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        return ["fake-cli", *(args or [])]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        return {}


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a clean registry; built-ins restored afterwards."""
    saved = dict(prov._PROVIDER_REGISTRY)
    prov._reset_for_tests()
    yield
    prov._reset_for_tests()
    prov._PROVIDER_REGISTRY.update(saved)


def test_register_and_get_roundtrips_a_provider():
    register_provider("fake", _FakeProvider)
    assert "fake" in provider_names()
    assert isinstance(get_provider("fake"), _FakeProvider)


def test_double_register_raises():
    register_provider("fake", _FakeProvider)
    with pytest.raises(prov.ProviderRegistryError, match="already registered"):
        register_provider("fake", _FakeProvider)


def test_unknown_provider_raises_valueerror():
    with pytest.raises(ValueError, match="Unknown provider: nope"):
        get_provider("nope")


def test_claude_arrives_through_its_declared_entry_point():
    """No built-in shortcut: claude registers the way every other surface does.

    It used to self-register ahead of entry-point discovery, so the declaration in
    ai-hats' own pyproject was never exercised and a broken one would have gone
    unnoticed. Deliberately unmonkeypatched — the real group, read from installed
    metadata (HATS-1826).
    """
    from ai_hats.constants import PROVIDER_CLAUDE
    from ai_hats.surfaces.claude.provider import ClaudeProvider

    prov._reset_for_tests()
    assert PROVIDER_CLAUDE in provider_names()
    assert isinstance(get_provider(PROVIDER_CLAUDE), ClaudeProvider)


class _FakeEntryPoint:
    """Stand-in for importlib.metadata.EntryPoint (only .name / .load used)."""

    def __init__(self, name: str, cls: type, *, boom: bool = False, dist=None):
        self.name = name
        self._cls = cls
        self._boom = boom
        self.dist = dist
        self.loaded = False

    def load(self):
        self.loaded = True
        if self._boom:
            raise RuntimeError("plugin import blew up")
        return self._cls


def test_out_of_tree_provider_is_discovered_via_entry_point(monkeypatch):
    ep = _FakeEntryPoint("plugin", _FakeProvider)
    monkeypatch.setattr(prov, "_provider_entry_points", lambda: [ep])
    prov._reset_for_tests()

    assert not ep.loaded  # lazy — nothing loaded before discovery runs
    prov._load_provider_entry_points()

    assert ep.loaded
    assert "plugin" in provider_names()
    assert isinstance(get_provider("plugin"), _FakeProvider)


def test_broken_entry_point_is_skipped_not_fatal(monkeypatch, caplog):
    bad = _FakeEntryPoint("broken", _FakeProvider, boom=True)
    good = _FakeEntryPoint("plugin", _FakeProvider)
    monkeypatch.setattr(prov, "_provider_entry_points", lambda: [bad, good])
    prov._reset_for_tests()

    with caplog.at_level("WARNING"):
        prov._load_provider_entry_points()  # must not raise

    assert "broken" not in provider_names()  # bad one skipped
    assert "plugin" in provider_names()  # good one still registered
    assert any("broken" in r.message for r in caplog.records)


def test_first_party_broken_entry_point_raises(monkeypatch):
    bad_dist = SimpleNamespace(name="ai-hats")
    bad = _FakeEntryPoint("firstparty", _FakeProvider, boom=True, dist=bad_dist)
    monkeypatch.setattr(prov, "_provider_entry_points", lambda: [bad])
    prov._reset_for_tests()

    with pytest.raises(RuntimeError, match="plugin import blew up"):
        prov._load_provider_entry_points()


def test_is_first_party_entry_point_helper():
    assert not _is_first_party_entry_point(SimpleNamespace())
    assert not _is_first_party_entry_point(SimpleNamespace(dist=None))
    assert not _is_first_party_entry_point(SimpleNamespace(dist=SimpleNamespace(name="acme-hats")))
    assert not _is_first_party_entry_point(
        SimpleNamespace(dist=SimpleNamespace(name="ai-hats-agy"))
    )
    assert _is_first_party_entry_point(SimpleNamespace(dist=SimpleNamespace(name="ai-hats")))
    assert _is_first_party_entry_point(SimpleNamespace(dist=SimpleNamespace(name="ai_hats")))
    assert _is_first_party_entry_point(
        SimpleNamespace(dist=SimpleNamespace(metadata={"Name": "ai-hats"}))
    )


def test_discovery_failure_is_non_fatal(monkeypatch):
    def _boom():
        raise RuntimeError("package metadata unavailable")

    monkeypatch.setattr(prov, "_provider_entry_points", _boom)
    prov._reset_for_tests()

    prov._load_provider_entry_points()  # swallows the error
    assert provider_names() == []  # nothing advertised, nothing registered


def test_pyproject_declares_provider_entry_point_group():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text())
    group = data["project"]["entry-points"][PROVIDER_ENTRY_POINT_GROUP]
    assert group == {
        "agy": "ai_hats.surfaces.agy.provider:AgyProvider",
        # HATS-1130: ec85f43d relocated ClaudeProvider into surfaces/.
        "claude": "ai_hats.surfaces.claude.provider:ClaudeProvider",
        # HATS-1826 folds the out-of-tree surface distributions into the area, so
        # the integrator now declares their entry points too.
        "cline": "ai_hats.surfaces.cline.provider:ClineProvider",
        "codex": "ai_hats.surfaces.codex.provider:CodexProvider",
        "opencode": "ai_hats.surfaces.opencode.provider:OpenCodeProvider",
    }


def test_legacy_gemini_alias_resolves_to_agy():
    register_provider("agy", _FakeProvider)
    provider = get_provider("gemini")
    assert isinstance(provider, _FakeProvider)


def test_a_known_surface_without_an_entry_point_refuses_immediately(monkeypatch):
    """ai-hats never installs a surface on the caller's behalf (HATS-1826).

    ``agy`` is in ``KNOWN_SURFACES``, and that used to be the trigger for a
    ``uv pip install`` before the lookup would refuse. With no entry point
    advertising it, the name is simply unknown.
    """
    monkeypatch.setattr(prov, "_provider_entry_points", lambda: [])
    prov._reset_for_tests()

    with pytest.raises(prov.UnknownProviderError, match="Unknown provider: agy"):
        get_provider("agy")
