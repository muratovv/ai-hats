"""E2E: every CLI entry-point exits clean on unknown ``--role <bogus>``.

Before HATS-507 the bare ``ai-hats --role <bogus>`` path raised a bare
``RuntimeError`` from inside the ``compose_role`` pipeline step. Click
had no special handling for it, so users saw a 9-frame Python traceback
and an exit code from Python's unhandled-exception path. No
discoverability for the typo.

HATS-507 fixed the bare-``ai-hats`` path. HATS-547 (S-CLI-20) closes the
same asymmetry for ``ai-hats execute`` (both ``--batch`` and
``--interactive``): pre-fix, ``execute_cmd`` did NOT catch
``RoleNotFoundError`` and re-leaked the traceback. The fix extracted
the renderer into ``cli/_helpers._handle_role_not_found`` and wired it
into ``execute_cmd`` too.

Setup contract (real subprocess + real ``ai-hats`` binary — satisfies
``dev_rule_e2e_gate`` for changes under ``src/ai_hats/cli/``):

1. ``tmp_project`` fixture bootstraps a role-less project pointed at the
   dev-venv ``ai-hats`` binary.
2. We invoke ``ai-hats <argv>`` for three CLI surfaces:
   - ``bare`` — ``--role <bogus>`` (HATS-507 regression guard).
   - ``execute-batch`` — ``execute --batch -r <bogus> --prompt ok``.
   - ``execute-interactive`` — ``execute -r <bogus> --prompt ok``
     (interactive is default; the exception fires in ``compose_role``
     BEFORE ``WrapRunner`` PTY-attaches, so this runs cleanly in a
     non-TTY subprocess — no provider binary is ever spawned).
3. Assertions per surface (identical contract):
   - exit code == 2 (Click's UsageError convention)
   - stderr names the bogus role
   - stderr contains the ``Available roles:`` header
   - stderr lists at least one known shipped role (``maintainer``)
   - combined stdout+stderr does NOT contain ``Traceback``

Fail-under-revert:

- Removing the typed raise in ``pipeline/steps/compose.py`` makes ALL
  three params fail (bare ``RuntimeError`` leaks).
- Removing the ``try/except`` in ``cli/__init__.py:_launch_session``
  makes only the ``bare`` param fail.
- Removing the ``try/except`` in ``cli/execute.py:execute_cmd`` makes
  only the ``execute-*`` params fail — exactly the surface HATS-547
  added.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


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
    ],
    ids=("bare", "execute-batch", "execute-interactive"),
)
def test_e2e_unknown_role_exits_clean_with_role_list(
    tmp_project, case_id: str, argv: tuple[str, ...],
) -> None:
    """``ai-hats <argv-with-bogus-role>`` → exit 2, friendly message, no traceback.

    Same contract across three CLI surfaces; see module docstring for
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
