"""e2e (HATS-513)

flow:   a developer inspecting dry-run handoff for hypothesis reflection
cmds:
    ai-hats reflect hypothesis --dry-run
expect: command writes handoff document to disk and exits cleanly without launching
        interactive session
why:    without dry-run support, developers cannot inspect hypothesis handoff documents
        without running sessions
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.observe]


def test_e2e_reflect_hypothesis_dry_run_writes_handoff(tmp_project) -> None:
    """``ai-hats reflect hypothesis --dry-run`` → exit 0 + handoff on disk."""
    result = tmp_project.run("reflect", "hypothesis", "--dry-run")

    assert result.exit_code == 0, (
        f"expected exit 0, got {result.exit_code}\n"
        f"stdout (tail 500):\n{result.stdout[-500:]}\n"
        f"stderr (tail 500):\n{result.stderr[-500:]}"
    )

    assert "Handoff written" in result.stdout, (
        f"stdout must announce the handoff path\nstdout:\n{result.stdout}"
    )

    # Dry-run must NOT proceed to Phase 1 launch.
    assert "Phase 1" not in result.stdout, (
        f"dry-run must short-circuit before Phase 1 launch\nstdout:\n{result.stdout}"
    )

    # Handoff file landed on disk under the reflect-all retros dir.
    handoff_dir = tmp_project.agent_dir / "sessions" / "retros" / "reflect-all"
    assert handoff_dir.is_dir(), f"handoff dir missing: {handoff_dir}"
    handoffs = list(handoff_dir.glob("*-handoff.md"))
    assert len(handoffs) == 1, (
        f"expected exactly one handoff under {handoff_dir}, got {len(handoffs)}: {handoffs}"
    )


def test_e2e_reflect_hypothesis_help_lists_flags(tmp_project) -> None:
    """``ai-hats reflect hypothesis --help`` advertises --headless and --dry-run.

    Cheaper gate-marker than the dry-run path — exercises only Click
    parsing, so it catches command-registration regressions even when
    the project's library / pipeline files are stale or absent.
    """
    result = tmp_project.run("reflect", "hypothesis", "--help")

    assert result.exit_code == 0, (
        f"reflect hypothesis --help failed: exit {result.exit_code}\nstderr:\n{result.stderr}"
    )

    for flag in ("--headless", "--dry-run"):
        assert flag in result.stdout, f"--help output missing {flag!r}\nstdout:\n{result.stdout}"

    assert "HATS-513" in result.stdout or "Two-phase HYP closure" in result.stdout
