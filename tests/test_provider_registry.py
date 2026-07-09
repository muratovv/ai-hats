"""Tests for the provider open-registry (HATS-870 / T10).

The closed ``PROVIDERS`` dict became an open registry: providers self-register
at import and third parties register via ``register_provider`` (or the
``ai_hats.providers`` entry-point group — see ``test_provider_entry_points``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats import providers as prov
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


def test_builtins_selfregister_in_gemini_claude_order():
    from ai_hats.constants import PROVIDER_CLAUDE, PROVIDER_GEMINI

    prov._register_builtins()
    # Order is a contract: cli/assembly.py detects providers in this order.
    assert provider_names() == [PROVIDER_GEMINI, PROVIDER_CLAUDE]
    assert isinstance(get_provider(PROVIDER_CLAUDE), prov.ClaudeProvider)
    assert isinstance(get_provider(PROVIDER_GEMINI), prov.GeminiProvider)
