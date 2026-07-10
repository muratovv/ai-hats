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
    RoleNotFoundError,
    build_composition_payload,
)


def _fake_assembler(available: list[str]) -> MagicMock:
    asm = MagicMock(name="assembler")
    asm.resolver.list_components.return_value = available
    return asm


def test_seam_routes_through_facade(tmp_path: Path):
    """HATS-501/456 invariant, relocated: the ONE composition goes through
    ``compose_for_role`` (single derivation point)."""
    fake_result = MagicMock(errors=[], merged_injection="ROLE PROMPT")
    asm = _fake_assembler(["judge"])
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
    asm = _fake_assembler(["judge"])
    with patch("ai_hats.assembler.Assembler", return_value=asm):
        with pytest.raises(RoleNotFoundError) as exc_info:
            build_composition_payload(tmp_path, role_override="ghost")
    assert exc_info.value.role == "ghost"
    assert exc_info.value.available == ["judge"]


def test_seam_raises_on_compose_errors(tmp_path: Path):
    fake_result = MagicMock(errors=["role not found"])
    asm = _fake_assembler(["ghost"])
    with patch("ai_hats.assembler.Assembler", return_value=asm), \
         patch("ai_hats.materialize.compose_for_role", return_value=fake_result):
        with pytest.raises(RuntimeError, match="failed to resolve role"):
            build_composition_payload(tmp_path, role_override="ghost")


def test_seam_lenient_mode_skips_raises(tmp_path: Path):
    """strict=False (retro reviewer spawn): no existence/errors raise —
    HATS-271 owns that failure mode downstream."""
    fake_result = MagicMock(errors=["broken"], merged_injection="")
    asm = _fake_assembler([])
    with patch("ai_hats.assembler.Assembler", return_value=asm), \
         patch("ai_hats.materialize.compose_for_role", return_value=fake_result), \
         patch("ai_hats.providers.get_provider", return_value=MagicMock()):
        payload = build_composition_payload(
            tmp_path, role_override="ghost", strict=False,
        )
    assert payload.result is fake_result


def test_seam_interactive_requires_provider(tmp_path: Path):
    """The former launch-step 'no provider configured' contract, relocated."""
    asm = _fake_assembler([])
    asm.project_config.provider = ""
    asm.project_config.active_role = ""
    asm.project_config.default_role = ""
    with patch("ai_hats.assembler.Assembler", return_value=asm):
        with pytest.raises(RuntimeError, match="no provider configured"):
            build_composition_payload(tmp_path, interactive=True)


def test_seam_carries_first_run_hooks_warning(tmp_path: Path):
    """HATS-970: a hooks warning raised by the first-run set_role side effect is
    carried on payload.startup_warnings so WrapRunner surfaces it in the hold."""
    fake_result = MagicMock(errors=[], merged_injection="ROLE PROMPT")
    asm = _fake_assembler(["judge"])
    asm.project_config.active_role = ""  # first-run → set_role fires
    asm.project_config.default_role = "judge"
    asm.project_config.provider = "gemini"

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
