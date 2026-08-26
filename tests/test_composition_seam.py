"""Compose-seam contract (HATS-865): the integrator composes ONCE.

``build_composition_payload`` owns what the ``compose_role`` step owned
pre-865: facade routing (HATS-456/501), explicit-role existence validation
(``RoleNotFoundError``, HATS-507), and the compose-errors raise. Structural
tests — Assembler/facade are mocked; the layered-composition behaviour is
pinned in ``tests/sessions/test_compose_overlay_propagation.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

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
    """HATS-501/456 invariant, relocated: the composition goes through
    ``compose_for_role`` (single derivation point).

    Routing only — NOT a pass count. The mocked assembler never reaches the real
    ``_get_overlay_provenance``, so this read green through all of HATS-1435,
    when a session start composed twice. Pass count lives in
    ``test_session_start_composes_exactly_once`` (real project)."""
    fake_result = MagicMock(errors=[], merged_injection="ROLE PROMPT")
    asm = _fake_assembler(["judge"], tmp_path)
    with (
        patch("ai_hats.assembler.Assembler", return_value=asm),
        patch("ai_hats.materialize.compose_for_role", return_value=fake_result) as facade,
        patch("ai_hats.surface_registry.get_surface", return_value=MagicMock()),
    ):
        payload = build_composition_payload(tmp_path, role_override="judge")
    facade.assert_called_once_with(asm, "judge", diagnostics=ANY)
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
    with (
        patch("ai_hats.assembler.Assembler", return_value=asm),
        patch("ai_hats.materialize.compose_for_role", return_value=fake_result),
    ):
        with pytest.raises(RuntimeError, match="failed to resolve role"):
            build_composition_payload(tmp_path, role_override="ghost")


def test_seam_lenient_mode_skips_raises(tmp_path: Path):
    """strict=False (retro reviewer spawn): no existence/errors raise —
    HATS-271 owns that failure mode downstream."""
    fake_result = MagicMock(errors=["broken"], merged_injection="")
    asm = _fake_assembler([], tmp_path)
    with (
        patch("ai_hats.assembler.Assembler", return_value=asm),
        patch("ai_hats.materialize.compose_for_role", return_value=fake_result),
        patch("ai_hats.surface_registry.get_surface", return_value=MagicMock()),
    ):
        payload = build_composition_payload(
            tmp_path,
            role_override="ghost",
            strict=False,
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
    with patch("ai_hats.assembler.Assembler", return_value=_provider_less_assembler(tmp_path)):
        with pytest.raises(MissingProviderError) as exc_info:
            build_composition_payload(tmp_path, interactive=True)
    _assert_missing_provider_contract(exc_info.value)


def test_preview_seam_requires_provider(tmp_path: Path):
    """HATS-1224: the dry-run/preview twin raises the same typed error."""
    with patch("ai_hats.assembler.Assembler", return_value=_provider_less_assembler(tmp_path)):
        with pytest.raises(MissingProviderError) as exc_info:
            build_preview_payload(tmp_path)
    _assert_missing_provider_contract(exc_info.value)


def _assert_missing_provider_contract(exc: MissingProviderError) -> None:
    # RuntimeError base keeps `config show-prompt`'s broad catch friendly
    # (HATS-1224), mirroring UnknownSurfaceError(ValueError).
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
    with (
        patch("ai_hats.assembler.Assembler", return_value=asm),
        patch("ai_hats.materialize.compose_for_role", return_value=fake_result),
        patch("ai_hats.surface_registry.get_surface") as get_surface,
    ):
        build_composition_payload(tmp_path, role_override="judge", **kwargs)
    return get_surface.call_args.args[0]


@pytest.mark.parametrize("interactive", [False, True])
def test_seam_explicit_provider_wins_over_cfg(tmp_path: Path, interactive: bool):
    """HATS-1218 R4: an explicit override beats ``cfg.provider`` on BOTH paths.

    Pre-fix the batch arm hard-read ``cfg.provider``, so ``ai-hats execute -p agy
    --batch`` accepted the flag and ran the configured surface in silence.
    """
    assert (
        _resolved_provider(
            tmp_path,
            provider_name="agy",
            interactive=interactive,
        )
        == "agy"
    )


@pytest.mark.parametrize("interactive", [False, True])
def test_seam_absent_override_falls_back_to_cfg(tmp_path: Path, interactive: bool):
    """HATS-1218 R4, other half: no override still resolves ``cfg.provider``."""
    assert (
        _resolved_provider(
            tmp_path,
            provider_name=None,
            interactive=interactive,
        )
        == "claude"
    )


def test_seam_batch_override_does_not_persist_active_role(tmp_path: Path):
    """HATS-1218: honouring ``-p`` on the batch path must not drag the
    interactive-only ``set_role`` write along with it — the whole point of the
    fix is that ``interactive`` keeps meaning (a) and stops meaning (b)."""
    fake_result = MagicMock(errors=[], merged_injection="ROLE PROMPT")
    asm = _fake_assembler([], tmp_path)
    asm.project_config.provider = "claude"
    asm.project_config.active_role = ""  # would be a first run, were it HITL
    asm.project_config.default_role = "judge"
    with (
        patch("ai_hats.assembler.Assembler", return_value=asm),
        patch("ai_hats.materialize.compose_for_role", return_value=fake_result),
        patch("ai_hats.surface_registry.get_surface", return_value=MagicMock()),
    ):
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

    def _set_role(role, provider, *, warnings_sink=None, result=None):
        if warnings_sink is not None:
            warnings_sink.append("core.hooksPath is already set to 'x' — not overwriting")

    asm.set_role.side_effect = _set_role

    with (
        patch("ai_hats.assembler.Assembler", return_value=asm),
        patch("ai_hats.materialize.compose_for_role", return_value=fake_result),
        patch("ai_hats.surface_registry.get_surface", return_value=MagicMock()),
    ):
        payload = build_composition_payload(tmp_path, interactive=True)

    assert any("core.hooksPath is already set" in w for w in payload.startup_warnings)
    assert "warnings_sink" in asm.set_role.call_args.kwargs


# --------------------------------------------------------------------- #
# HATS-1435 — one session start composes ONCE, measured on a REAL project
#
# The mocked tests above cannot see this: with a MagicMock assembler,
# `_composition_snapshot` never reaches the real `_get_overlay_provenance`,
# so its second compose is invisible. These use a real Assembler.
# --------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


def _real_project(tmp_path: Path, *, active_role: str | None) -> Path:
    """Project on this repo's real library. ``active_role=None`` leaves the
    first-run branch armed, so ``_maybe_sync_active_role`` fires ``set_role``."""
    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role=active_role or "",
        default_role="maintainer",
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[LIBRARY_DIR]).init()
    return project


@contextmanager
def _compose_spy():
    """Count REAL composes. Patches both bindings: ``assembler.py`` imports
    ``compose_for_role`` at module level, ``composition_seam.py`` lazily inside
    functions — patching one alone silently misses the other's call sites."""
    import ai_hats.materialize as materialize

    real = materialize.compose_for_role
    calls: list[str] = []

    def spy(assembler, role_name, *a, **kw):
        calls.append(role_name)
        return real(assembler, role_name, *a, **kw)

    with (
        patch("ai_hats.materialize.compose_for_role", spy),
        patch("ai_hats.assembler.compose_for_role", spy),
    ):
        yield calls


def test_session_start_composes_exactly_once(tmp_path: Path):
    """HATS-1435: the seam already holds the result `_composition_snapshot`
    needs; recomposing re-reads the whole library and doubles every load-time
    diagnostic, so one WARN reads as two."""
    project = _real_project(tmp_path, active_role="maintainer")
    with _compose_spy() as calls:
        build_composition_payload(project, interactive=True)
    assert calls == ["maintainer"], f"expected ONE compose pass, got {len(calls)}: {calls}"


def test_first_run_session_start_composes_exactly_once(tmp_path: Path):
    """HATS-1435: with no ``active_role`` the seam also fires ``set_role``,
    which composed a third time — so the first launch a user ever sees was the
    noisiest one."""
    project = _real_project(tmp_path, active_role=None)
    with _compose_spy() as calls:
        build_composition_payload(project, interactive=True)
    assert calls == ["maintainer"], f"expected ONE compose pass, got {len(calls)}: {calls}"


def test_snapshot_and_provenance_agree_on_effective_traits(tmp_path: Path):
    """HATS-1435: `_composition_snapshot` and `_get_overlay_provenance` each
    walked base+overlays to the same effective-trait list. Two copies that must
    agree and nothing pinned that they did — so drift would land silently."""
    from ai_hats.assembler import Assembler
    from ai_hats.models import OverlayConfig, ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
        customizations={
            "maintainer": OverlayConfig(
                add_traits=["trait-researcher-mindset"],  # already present → no-op add
                remove_traits=["dev::shell"],
            )
        },
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[LIBRARY_DIR]).init()

    snapshot = build_composition_payload(project, interactive=False).snapshot

    assert "dev::shell" not in snapshot["traits"], "overlay remove not applied"
    assert set(snapshot["traits"]) == set(snapshot["provenance"]["traits"]), (
        "the two effective-trait walks disagree: "
        f"snapshot={sorted(snapshot['traits'])} "
        f"provenance={sorted(snapshot['provenance']['traits'])}"
    )


def test_runtime_role_composition_overlay_and_snapshot(tmp_path: Path):
    """HATS-1456: runtime role composition adds overlay, updates snapshot and provenance."""
    project = _real_project(tmp_path, active_role="maintainer")
    payload = build_composition_payload(
        project, role_override="maintainer + ai-hats-framework", interactive=False
    )

    assert "ai-hats-framework" in payload.snapshot["traits"]
    assert payload.snapshot["provenance"]["traits"]["ai-hats-framework"] == "runtime"
    assert payload.snapshot["runtime"] == {
        "spec": "maintainer + ai-hats-framework",
        "add": ["ai-hats-framework"],
        "remove": [],
    }


def test_runtime_role_composition_role_in_second_position(tmp_path: Path):
    """HATS-1456: mixing in a role raises RoleSpecError."""
    from ai_hats.role_spec import RoleSpecError

    project = _real_project(tmp_path, active_role="maintainer")
    with pytest.raises(RoleSpecError, match="'assistant' is a role"):
        build_composition_payload(
            project, role_override="maintainer + assistant", interactive=False
        )


def test_runtime_role_composition_unknown_component(tmp_path: Path):
    """HATS-1456: unknown component name raises RoleSpecError with suggestions."""
    from ai_hats.role_spec import RoleSpecError

    project = _real_project(tmp_path, active_role="maintainer")
    with pytest.raises(RoleSpecError, match="'non-existent' is not a known trait, rule or skill"):
        build_composition_payload(
            project, role_override="maintainer + non-existent", interactive=False
        )


def test_runtime_role_composition_ambiguous_component(tmp_path: Path):
    """HATS-1456: component name matching multiple kinds raises RoleSpecError."""
    from ai_hats.composition_seam import _runtime_overlay
    from ai_hats.models import ComponentType
    from ai_hats.role_spec import RoleSpec, RoleSpecError

    resolver = MagicMock()
    resolver.list_components.side_effect = lambda ctype: {
        ComponentType.TRAIT: ["shared-name"],
        ComponentType.SKILL: ["shared-name"],
        ComponentType.RULE: [],
        ComponentType.ROLE: [],
    }[ctype]

    spec = RoleSpec(
        role="maintainer", adds=("shared-name",), removes=(), raw="maintainer + shared-name"
    )
    with pytest.raises(
        RoleSpecError, match="'shared-name' is ambiguous — it is both a trait and a skill"
    ):
        _runtime_overlay(resolver, spec)
