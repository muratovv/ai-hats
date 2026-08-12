"""e2e (HATS-507, HATS-545, HATS-547)

flow:   a developer specifying an unknown role name on CLI
cmds:
    ai-hats --role definitely-not-a-real-role
expect: CLI exits with code 2 listing available roles without printing raw Python
        traceback
why:    without role error handling, typos in role parameters dump raw RuntimeError
        tracebacks to user
"""

from __future__ import annotations

import pytest


# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]


_BOGUS = "definitely-not-a-real-role"


@pytest.mark.parametrize(
    ("case_id", "argv"),
    [
        ("bare", ("--role", _BOGUS)),
        (
            "execute-batch",
            ("execute", "--batch", "-r", _BOGUS, "--prompt", "ok"),
        ),
        (
            "execute-interactive",
            ("execute", "-r", _BOGUS, "--prompt", "ok"),
        ),
        ("agent", ("agent", _BOGUS, "--task", "ok")),
    ],
    ids=("bare", "execute-batch", "execute-interactive", "agent"),
)
def test_e2e_unknown_role_exits_clean_with_role_list(
    tmp_project,
    case_id: str,
    argv: tuple[str, ...],
) -> None:
    """``ai-hats <argv-with-bogus-role>`` → exit 2, friendly message, no traceback.

    Same contract across four CLI surfaces; see module docstring for
    the per-param revert-check expectation.
    """
    result = tmp_project.run(*argv, timeout=10.0)

    # Exit 2 is Click's UsageError convention; HATS-507 / HATS-547 mirror it.
    assert result.exit_code == 2, (
        f"[{case_id}] expected exit 2, got {result.exit_code}\n"
        f"stdout (tail 500):\n{result.stdout[-500:]}\n"
        f"stderr (tail 500):\n{result.stderr[-500:]}"
    )

    # Names the bogus role + heads the available-roles list + carries
    # at least one shipped role name. We pick ``maintainer`` because it
    # is a stable usage-tier role that has shipped since HATS-433.
    for marker in (
        _BOGUS,
        "Available roles:",
        "maintainer",
        "ai-hats list roles",
    ):
        assert marker in result.stderr, (
            f"[{case_id}] stderr missing marker {marker!r}\n"
            f"stderr (tail 800):\n{result.stderr[-800:]}"
        )

    # No traceback leak in either stream — the whole point of the change.
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"[{case_id}] traceback leaked to user-facing output:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
