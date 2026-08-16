"""The root group renders every registered typed error friendly (HATS-1228).

Friendly rendering used to be per-site opt-in: each launch surface caught the
compose-seam errors itself, so a surface that forgot one (``cli/reflect.py``,
five compose sites and no arms) leaked a traceback. Dispatch now lives on the
root group, and these tests pin the contract at the group boundary — including
the in-process ``CliRunner`` path, which bypasses ``main_entry`` (HATS-791) and
is therefore the one path a ``main_entry``-only boundary could not cover.

The end-to-end proof on real surfaces is
``tests/e2e/test_reflect_friendly_errors.py`` and the provider/role e2e files.
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from ai_hats.cli import _PassthroughGroup
from ai_hats.composition_seam import MissingProviderError, RoleNotFoundError
from ai_hats.paths import NotAnAiHatsProjectError
from ai_hats.providers import UnknownProviderError
from ai_hats.self_heal import ProviderInstallationError


def _cases():
    from pathlib import Path

    return [
        pytest.param(RoleNotFoundError("ghost", ["judge"]), "ghost", id="role"),
        pytest.param(
            UnknownProviderError("nope", ["claude"]),
            "nope",
            id="unknown-provider",
        ),
        pytest.param(
            MissingProviderError(["claude"]),
            "no provider configured",
            id="missing-provider",
        ),
        pytest.param(
            ProviderInstallationError("registry denied"),
            "registry denied",
            id="provider-install",
        ),
        pytest.param(
            NotAnAiHatsProjectError(Path("/tmp/nowhere")),
            "nowhere",
            id="not-a-project",
        ),
    ]


def _root(exc: Exception) -> click.Group:
    """A root group shaped like ``ai-hats``: a callback that runs bare, plus a
    subcommand. Both raise ``exc`` so one case covers both invocation paths."""

    @click.group(cls=_PassthroughGroup, invoke_without_command=True)
    @click.pass_context
    def root(ctx: click.Context) -> None:
        if ctx.invoked_subcommand is None:
            raise exc

    @root.command("sub")
    def sub() -> None:
        raise exc

    return root


def _output(result) -> str:
    return result.output + (getattr(result, "stderr", "") or "")


@pytest.mark.parametrize(("exc", "marker"), _cases())
@pytest.mark.parametrize("argv", [[], ["sub"]], ids=["bare", "subcommand"])
def test_group_renders_registered_error_as_exit_2(exc, marker, argv) -> None:
    """Registered error → exit 2, its marker on stderr, no traceback."""
    result = CliRunner().invoke(_root(exc), argv)

    assert result.exit_code == 2, f"expected exit 2, got {result.exit_code}"
    assert marker in _output(result), f"output missing marker {marker!r}: {_output(result)!r}"
    assert "Traceback" not in _output(result)


def test_unregistered_error_still_propagates() -> None:
    """The group is a renderer, not a catch-all: anything unregistered keeps its
    traceback, so a real bug is never swallowed into a tidy exit 2."""
    boom = ValueError("some internal bug")
    result = CliRunner().invoke(_root(boom), [])

    assert result.exit_code == 1
    assert result.exception is boom


def test_missing_provider_dispatches_by_isinstance_not_exact_type() -> None:
    """``MissingProviderError`` subclasses ``RuntimeError`` (HATS-1224), and a
    future error may subclass a registered one — dispatch must walk isinstance,
    not look up ``type(exc)``."""

    class NarrowerMissingProvider(MissingProviderError):
        pass

    result = CliRunner().invoke(_root(NarrowerMissingProvider(["claude"])), [])

    assert result.exit_code == 2
    assert "no provider configured" in _output(result)


def test_real_main_group_carries_the_dispatch() -> None:
    """Every other test here builds a synthetic root, so none of them notice if
    the shipped ``main`` stops using the dispatching group class."""
    from ai_hats.cli import main

    assert isinstance(main, _PassthroughGroup), (
        f"ai_hats.cli.main is {type(main).__name__}, not _PassthroughGroup — "
        "the friendly-error dispatch is unmounted from the real CLI"
    )
