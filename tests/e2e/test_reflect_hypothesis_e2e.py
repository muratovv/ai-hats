"""E2E: ``ai-hats reflect hypothesis --dry-run`` produces a handoff.

E2E gate: HATS-513 touches ``src/ai_hats/cli/reflect.py`` (new
``reflect hypothesis`` command). Per ``dev_rule_e2e_gate``, the CLI
contract must be covered by a real subprocess + real ``ai-hats`` binary
test — not an in-process ``CliRunner``.

Setup contract:

1. ``tmp_project`` fixture bootstraps a role-less project pointed at the
   dev-venv ``ai-hats`` binary (which is editable-installed from
   ``<repo_root>/src``, so the new ``reflect hypothesis`` command is
   reachable).
2. We invoke ``ai-hats reflect hypothesis --dry-run`` via
   :class:`Project.run`.
3. Assertions:
   - exit code == 0
   - stdout names the handoff path
   - the handoff file exists on disk
   - stdout does NOT report Phase 1 launch (dry-run short-circuit)

Fail-under-revert: removing the new ``reflect hypothesis`` Click command
from ``src/ai_hats/cli/reflect.py`` makes the subprocess exit non-zero
(Click reports "no such command: hypothesis") and this test goes red.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


def test_e2e_reflect_hypothesis_dry_run_writes_handoff(tmp_project) -> None:
    """``ai-hats reflect hypothesis --dry-run`` → exit 0 + handoff on disk."""
    result = tmp_project.run("reflect", "hypothesis", "--dry-run")

    assert result.exit_code == 0, (
        f"expected exit 0, got {result.exit_code}\n"
        f"stdout (tail 500):\n{result.stdout[-500:]}\n"
        f"stderr (tail 500):\n{result.stderr[-500:]}"
    )

    assert "Handoff written" in result.stdout, (
        "stdout must announce the handoff path\n"
        f"stdout:\n{result.stdout}"
    )

    # Dry-run must NOT proceed to Phase 1 launch.
    assert "Phase 1" not in result.stdout, (
        "dry-run must short-circuit before Phase 1 launch\n"
        f"stdout:\n{result.stdout}"
    )

    # Handoff file landed on disk under the reflect-all retros dir.
    handoff_dir = (
        tmp_project.agent_dir / "sessions" / "retros" / "reflect-all"
    )
    assert handoff_dir.is_dir(), (
        f"handoff dir missing: {handoff_dir}"
    )
    handoffs = list(handoff_dir.glob("*-handoff.md"))
    assert len(handoffs) == 1, (
        f"expected exactly one handoff under {handoff_dir}, "
        f"got {len(handoffs)}: {handoffs}"
    )


def test_e2e_reflect_hypothesis_help_lists_flags(tmp_project) -> None:
    """``ai-hats reflect hypothesis --help`` advertises --headless and --dry-run.

    Cheaper gate-marker than the dry-run path — exercises only Click
    parsing, so it catches command-registration regressions even when
    the project's library / pipeline files are stale or absent.
    """
    result = tmp_project.run("reflect", "hypothesis", "--help")

    assert result.exit_code == 0, (
        f"reflect hypothesis --help failed: exit {result.exit_code}\n"
        f"stderr:\n{result.stderr}"
    )

    for flag in ("--headless", "--dry-run"):
        assert flag in result.stdout, (
            f"--help output missing {flag!r}\n"
            f"stdout:\n{result.stdout}"
        )

    # Cite the ADR — also doubles as a regression marker for
    # accidental docstring drift.
    assert "ADR-0007" in result.stdout
