"""Compose-seam contract (HATS-865): the integrator composes ONCE.

``build_composition_payload`` owns what the ``compose_role`` step owned
pre-865: facade routing (HATS-456/501), explicit-role existence validation
(``RoleNotFoundError``, HATS-507), and the compose-errors raise. Structural
tests — Assembler/facade are mocked; the layered-composition behaviour is
pinned in ``tests/pipeline/test_compose_overlay_propagation.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_hats.composition_seam import (
    MissingProviderError,
    RoleNotFoundError,
    build_composition_payload,
    build_preview_payload,
)


def _fake_assembler(available: list[str], project_dir: Path) -> MagicMock:
    """Assembler double. ``project_dir`` MUST be a real Path: unmocked code
    paths do filesystem/YAML work on it, and a MagicMock there makes PyYAML
    iterate a mock as a stream until the process dies (HATS-1203)."""
    asm = MagicMock(name="assembler")
    asm.resolver.list_components.return_value = available
    asm.project_dir = project_dir
    return asm


def test_seam_routes_through_facade(tmp_path: Path):
    """HATS-501/456 invariant, relocated: the ONE composition goes through
    ``compose_for_role`` (single derivation point)."""
    fake_result = MagicMock(errors=[], merged_injection="ROLE PROMPT")
    asm = _fake_assembler(["judge"], tmp_path)
    with patch("ai_hats.assembler.Assembler", return_value=asm), \
         patch("ai_hats.materialize.compose_for_role",
               return_value=fake_result) as facade, \
         patch("ai_hats.providers.get_provider", return_value=MagicMock()):
        payload = build_composition_payload(tmp_path, role_override="judge")
    facade.assert_called_once_with(asm, "judge")
    assert payload.result is fake_result
    assert payload.effective_role == "judge"


def test_seam_raises_role_not_found_for_explicit_role(tmp_path: Path):
    """HATS-507 UX contract survives the move: unknown explicit role raises
    the typed error BEFORE any pipeline runs (cli renders 'Available roles')."""
    asm = _fake_assembler(["judge"], tmp_path)
    with patch("ai_hats.assembler.Assembler", return_value=asm):
        with pytest.raises(RoleNotFoundError) as exc_info:
            build_composition_payload(tmp_path, role_override="ghost")
    assert exc_info.value.role == "ghost"
    assert exc_info.value.available == ["judge"]


def test_seam_raises_on_compose_errors(tmp_path: Path):
    fake_result = MagicMock(errors=["role not found"])
    asm = _fake_assembler(["ghost"], tmp_path)
    with patch("ai_hats.assembler.Assembler", return_value=asm), \
         patch("ai_hats.materialize.compose_for_role", return_value=fake_result):
        with pytest.raises(RuntimeError, match="failed to resolve role"):
            build_composition_payload(tmp_path, role_override="ghost")


def test_seam_lenient_mode_skips_raises(tmp_path: Path):
    """strict=False (retro reviewer spawn): no existence/errors raise —
    HATS-271 owns that failure mode downstream."""
    fake_result = MagicMock(errors=["broken"], merged_injection="")
    asm = _fake_assembler([], tmp_path)
    with patch("ai_hats.assembler.Assembler", return_value=asm), \
         patch("ai_hats.materialize.compose_for_role", return_value=fake_result), \
         patch("ai_hats.providers.get_provider", return_value=MagicMock()):
        payload = build_composition_payload(
            tmp_path, role_override="ghost", strict=False,
        )
    assert payload.result is fake_result


def _provider_less_assembler(project_dir: Path) -> MagicMock:
    """Assembler whose cfg carries an explicitly emptied ``provider:``."""
    asm = _fake_assembler(["judge"], project_dir)
    asm.project_config.provider = ""
    asm.project_config.active_role = "judge"
    asm.project_config.default_role = "judge"
    return asm


def test_seam_interactive_requires_provider(tmp_path: Path):
    """The former launch-step 'no provider configured' contract, relocated."""
    with patch(
        "ai_hats.assembler.Assembler", return_value=_provider_less_assembler(tmp_path)
    ):
        with pytest.raises(MissingProviderError) as exc_info:
            build_composition_payload(tmp_path, interactive=True)
    _assert_missing_provider_contract(exc_info.value)


def test_preview_seam_requires_provider(tmp_path: Path):
    """HATS-1224: the dry-run/preview twin raises the same typed error."""
    with patch(
        "ai_hats.assembler.Assembler", return_value=_provider_less_assembler(tmp_path)
    ):
        with pytest.raises(MissingProviderError) as exc_info:
            build_preview_payload(tmp_path)
    _assert_missing_provider_contract(exc_info.value)


def _assert_missing_provider_contract(exc: MissingProviderError) -> None:
    # RuntimeError base keeps `config show-prompt`'s broad catch friendly
    # (HATS-1224), mirroring UnknownProviderError(ValueError).
    assert isinstance(exc, RuntimeError)
    assert "no provider configured" in str(exc)
    assert "claude" in exc.available


def _provider_seam_assembler(project_dir: Path) -> MagicMock:
    """Assembler whose cfg names ``claude`` and whose role is already active.

    An active role keeps ``first_run_hitl`` false on BOTH paths, so these tests
    isolate provider resolution from the ``set_role`` side effect.
    """
    asm = _fake_assembler(["judge"], project_dir)
    asm.project_config.provider = "claude"
    asm.project_config.active_role = "judge"
    asm.project_config.default_role = "judge"
    return asm


def _resolved_provider(tmp_path: Path, **kwargs) -> str:
    """The name ``build_composition_payload`` actually resolves a provider for."""
    fake_result = MagicMock(errors=[], merged_injection="ROLE PROMPT")
    asm = _provider_seam_assembler(tmp_path)
    with patch("ai_hats.assembler.Assembler", return_value=asm), \
         patch("ai_hats.materialize.compose_for_role", return_value=fake_result), \
         patch("ai_hats.providers.get_provider") as get_provider:
        build_composition_payload(tmp_path, role_override="judge", **kwargs)
    return get_provider.call_args.args[0]


@pytest.mark.parametrize("interactive", [False, True])
def test_seam_explicit_provider_wins_over_cfg(tmp_path: Path, interactive: bool):
    """HATS-1218 R4: an explicit override beats ``cfg.provider`` on BOTH paths.

    Pre-fix the batch arm hard-read ``cfg.provider``, so ``ai-hats execute -p agy
    --batch`` accepted the flag and ran the configured surface in silence.
    """
    assert _resolved_provider(
        tmp_path, provider_name="agy", interactive=interactive,
    ) == "agy"


@pytest.mark.parametrize("interactive", [False, True])
def test_seam_absent_override_falls_back_to_cfg(tmp_path: Path, interactive: bool):
    """HATS-1218 R4, other half: no override still resolves ``cfg.provider``."""
    assert _resolved_provider(
        tmp_path, provider_name=None, interactive=interactive,
    ) == "claude"


def test_seam_batch_override_does_not_persist_active_role(tmp_path: Path):
    """HATS-1218: honouring ``-p`` on the batch path must not drag the
    interactive-only ``set_role`` write along with it — the whole point of the
    fix is that ``interactive`` keeps meaning (a) and stops meaning (b)."""
    fake_result = MagicMock(errors=[], merged_injection="ROLE PROMPT")
    asm = _fake_assembler([], tmp_path)
    asm.project_config.provider = "claude"
    asm.project_config.active_role = ""  # would be a first run, were it HITL
    asm.project_config.default_role = "judge"
    with patch("ai_hats.assembler.Assembler", return_value=asm), \
         patch("ai_hats.materialize.compose_for_role", return_value=fake_result), \
         patch("ai_hats.providers.get_provider", return_value=MagicMock()):
        build_composition_payload(tmp_path, provider_name="agy", interactive=False)
    asm.set_role.assert_not_called()


def test_seam_carries_first_run_hooks_warning(tmp_path: Path):
    """HATS-970: a hooks warning raised by the first-run set_role side effect is
    carried on payload.startup_warnings so WrapRunner surfaces it in the hold."""
    fake_result = MagicMock(errors=[], merged_injection="ROLE PROMPT")
    asm = _fake_assembler(["judge"], tmp_path)
    asm.project_config.active_role = ""  # first-run → set_role fires
    asm.project_config.default_role = "judge"
    asm.project_config.provider = "agy"

    def _set_role(role, provider, *, warnings_sink=None):
        if warnings_sink is not None:
            warnings_sink.append("core.hooksPath is already set to 'x' — not overwriting")

    asm.set_role.side_effect = _set_role

    with patch("ai_hats.assembler.Assembler", return_value=asm), \
         patch("ai_hats.materialize.compose_for_role", return_value=fake_result), \
         patch("ai_hats.providers.get_provider", return_value=MagicMock()):
        payload = build_composition_payload(tmp_path, interactive=True)

    assert any("core.hooksPath is already set" in w for w in payload.startup_warnings)
    assert "warnings_sink" in asm.set_role.call_args.kwargs
