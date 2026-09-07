"""``execute`` refuses a flag its mode cannot act on (HATS-1218).

The provider override was one instance of a class: a knob the CLI accepts and
silently drops. ``WrapRunner.run`` takes only ``(extra_args, tags,
pty_tap_factory)``, so ``--isolation/--ticket/--json`` are inert under
``--interactive``; ``SubAgentRunner.run`` takes no ``extra_args``, so ``--batch``
swallowed them. The help text hedged four of them with "(batch only)" and
enforced none.

``--model`` was in that set and left it (HATS-1891): the refusal claimed the HITL
runner could not act on it, when the provider's own ``--model`` riding
``extra_args`` had always worked.

The negative controls matter as much as the refusals: a default must never be
mistaken for a passed flag — which is why the guard reads Click's
``ParameterSource`` rather than comparing values (``--isolation`` defaults to
``discard``, so a value check would refuse every interactive launch).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        pytest.param(["--isolation", "squash"], "--isolation", id="isolation"),
        pytest.param(["--ticket", "HATS-1"], "--ticket", id="ticket"),
        pytest.param(["--json"], "--json", id="json"),
    ],
)
def test_batch_only_flag_is_refused_under_interactive(
    project_dir: Path,
    mock_runners,
    flag,
    expected,
):
    res = CliRunner().invoke(main, ["execute", "--role", "judge", *flag])
    assert res.exit_code == 2, res.output
    assert expected in res.output
    assert "batch-only" in res.output
    assert not mock_runners["wrap_calls"], "refused launch still spawned a runner"


def test_model_is_acted_on_under_interactive(project_dir: Path, mock_runners):
    """HATS-1891: ``--model`` left the batch-only set — the HITL runner acts on it.

    The three flags still in the parametrize above are this test's positive
    control: if they stopped being refused, the refusal itself would be gone and
    this assertion would prove nothing.
    """
    res = CliRunner().invoke(main, ["execute", "--role", "judge", "--model", "opus"])
    assert res.exit_code == 0, res.output
    assert mock_runners["wrap_calls"][0]["extra_args"] == ["--model", "opus"]


def test_extra_args_are_refused_under_batch(project_dir: Path, mock_runners):
    res = CliRunner().invoke(
        main,
        ["execute", "--role", "session-reviewer", "--batch", "--resume"],
    )
    assert res.exit_code == 2, res.output
    assert "interactive-only" in res.output
    assert not mock_runners["sub_calls"], "refused launch still spawned a runner"


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["execute", "--role", "judge"], id="bare-interactive"),
        pytest.param(["execute", "--role", "judge", "--resume"], id="extra-args-ok"),
        pytest.param(["execute", "--role", "judge", "--tag", "k=v"], id="tag-works-in-both"),
    ],
)
def test_interactive_launches_when_no_batch_only_flag_is_passed(
    project_dir: Path,
    mock_runners,
    argv,
):
    """Defaults must not read as passed — ``--isolation`` defaults to ``discard``."""
    res = CliRunner().invoke(main, argv)
    assert res.exit_code == 0, res.output
    assert len(mock_runners["wrap_calls"]) == 1


@pytest.mark.parametrize(
    "flag",
    [
        pytest.param(["--model", "opus"], id="model"),
        pytest.param(["--isolation", "squash"], id="isolation"),
        pytest.param(["--ticket", "HATS-1"], id="ticket"),
        pytest.param(["--json"], id="json"),
    ],
)
def test_the_same_flags_are_accepted_under_batch(
    project_dir: Path,
    mock_runners,
    flag,
):
    res = CliRunner().invoke(
        main,
        ["execute", "--role", "session-reviewer", "--batch", *flag],
    )
    assert res.exit_code == 0, res.output
    assert len(mock_runners["sub_calls"]) == 1
