"""``ai-hats wait`` flag guards: what is refused and what must pass (HATS-1452).

Both halves of one guard. Splitting them across tiers would let the positive
half drift from the negative one, so they moved together (HATS-1493) out of
``tests/e2e/`` — arg validation happens before any polling, needs no process,
and ``wait`` had no unit coverage at all before this file.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from ai_hats.cli.wait import wait_cmd


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
    ],
    ids=["timeout-zero-waits-forever", "fractional-and-exponential-values"],
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
