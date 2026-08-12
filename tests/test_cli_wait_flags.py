"""``ai-hats wait`` flag guards: what is refused and what must pass (HATS-1452).

Both halves of one guard. Splitting them across tiers would let the positive
half drift from the negative one, so they moved together (HATS-1493) out of
``tests/e2e/`` — arg validation happens before any polling, needs no process,
and ``wait`` had no unit coverage at all before this file.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from ai_hats.cli.wait import PROBE_TIMEOUT_S, _probe_budget, wait_cmd


@pytest.mark.parametrize(
    ("extra_args", "bad_flag"),
    [
        (("--poll", "0", "--timeout", "3"), "--poll"),
        (("--poll", "-5", "--timeout", "3"), "--poll"),
        (("--poll", "-0.5", "--timeout", "3"), "--poll"),
        (("--poll", "nan", "--timeout", "3"), "--poll"),
        (("--poll", "inf", "--timeout", "3"), "--poll"),
        (("--poll", "0.2", "--timeout", "-1"), "--timeout"),
        (("--poll", "0.2", "--timeout", "nan"), "--timeout"),
        (("--poll", "0.2", "--timeout", "inf"), "--timeout"),
        (("--probe-timeout", "-1"), "--probe-timeout"),
        (("--probe-timeout", "nan"), "--probe-timeout"),
        (("--probe-timeout", "inf"), "--probe-timeout"),
    ],
)
def test_non_positive_poll_or_bad_timeout_rejected_at_input(
    extra_args: tuple[str, ...], bad_flag: str
) -> None:
    """``--poll <= 0`` (or non-finite) and a negative/non-finite ``--timeout``
    must be refused before polling starts — else they collapse into a busy-loop
    (``time.sleep(max(nap, 0.0))`` naps for 0s on any of these). The predicate
    is already-true ``true`` in every case: without the guard this exits 0
    immediately, so a green run means the guard is missing, not that the wait
    "happened to be fast".
    """
    result = CliRunner().invoke(wait_cmd, ["--until-cmd", "true", *extra_args])

    assert result.exit_code == 2, f"expected exit 2, got {result.exit_code}: {result.output}"
    assert bad_flag in result.output, f"output should name {bad_flag}: {result.output}"


@pytest.mark.parametrize(
    "extra_args",
    [
        ("--poll", "0.2", "--timeout", "0"),
        ("--poll", "1.1", "--timeout", "1e2"),
        ("--poll", "0.2", "--probe-timeout", "0"),
    ],
    ids=[
        "timeout-zero-waits-forever",
        "fractional-and-exponential-values",
        "probe-timeout-zero-is-the-opt-out",
    ],
)
def test_legal_poll_and_timeout_values_accepted(extra_args: tuple[str, ...]) -> None:
    """Legal float values must pass the guard untouched: ``--timeout 0`` keeps
    meaning "wait forever" rather than tripping the negative-timeout guard, and
    fractional / scientific-notation values (both valid ``float`` syntax) are
    not rejected by the finite/positive check.
    """
    result = CliRunner().invoke(wait_cmd, ["--until-cmd", "true", *extra_args])

    assert result.exit_code == 0, f"legal values refused: {result.output}"
    assert "--poll" not in result.output and "--timeout" not in result.output, (
        f"a guard named a flag on legal input: {result.output}"
    )


def test_probe_is_bounded_by_default_when_nothing_else_bounds_it() -> None:
    """The default must protect the unbounded wait, not only the deadlined one.

    ``--timeout 0`` is the common call and leaves no deadline to derive a probe
    bound from, so if the default were 0 (off) the HATS-1598 defect would
    survive in the configuration most people run. Asserting the wiring too: an
    option default that drifts off the constant silently un-bounds it.
    """
    budget, by_deadline = _probe_budget(PROBE_TIMEOUT_S, deadline=None)

    assert budget == PROBE_TIMEOUT_S, f"no deadline left the probe unbounded: {budget}"
    assert not by_deadline, "with no deadline, the bound cannot be attributed to one"
    assert PROBE_TIMEOUT_S > 0, "a non-positive default is the opt-out, not a bound"

    default = next(p.default for p in wait_cmd.params if p.name == "probe_timeout")
    assert default == PROBE_TIMEOUT_S, (
        f"--probe-timeout defaults to {default}, not the module constant {PROBE_TIMEOUT_S}"
    )
