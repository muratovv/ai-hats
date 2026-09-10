"""e2e (HATS-546)

flow:   a developer running role coherence audit command
cmds:
    ai-hats reflect role maintainer
expect: pre-flight composes and serializes target role manifest and launches role-judge
        session
why:    without composition materialization, role-judge auditor lacks structured role
        breakdown to audit
"""

from __future__ import annotations

import pytest

from _helpers.hitl import drive_bare_hitl, strip_ansi
from _helpers.project import Project


pytestmark = [pytest.mark.integration, pytest.mark.observe]


# ``maintainer`` ships in core (``library/core/roles/maintainer/``)
# since HATS-433; reliable target for the audit user-way.
TARGET_ROLE = "maintainer"

# Python compose + materialize + PTY launch + ``/exit`` ≈ 5-15s on
# warm cache; envelope buffer for cold network / first-load.
REFLECT_ROLE_TIMEOUT = 30.0


def test_reflect_role_materializes_target_composition(
    tmp_project: Project,
    requires_claude_auth,  # noqa: ARG001 — skip-marker fixture
) -> None:
    """User-way smoke: composed-role manifest on disk + clean PTY exit."""
    result = (
        drive_bare_hitl(
            tmp_project,
            subcommand_args=("reflect", "role", TARGET_ROLE),
            timeout=REFLECT_ROLE_TIMEOUT,
        )
        .expect_no_hang()
        .expect_exit_in({0, 130})
    )

    # ---- Pre-flight materialization (deterministic, Python-side) ----
    # Path contract from PipelineHarness.namespace:
    #   <ai_hats_dir>/sessions/runs/pipeline_runs/<pipeline>/<session_id>/
    # plus reflect.py's ``h.namespace / "composed" / target_role``.
    pipeline_runs = tmp_project.agent_dir / "sessions" / "runs" / "pipeline_runs" / "reflect-role"
    composed = list(pipeline_runs.glob(f"*/composed/{TARGET_ROLE}/manifest.yaml"))
    assert len(composed) == 1, (
        f"expected exactly one composed manifest under {pipeline_runs}, "
        f"got {len(composed)}: {composed}"
    )
    # Manifest is a YAML dict written by _materialize_target_composition;
    # non-zero size means the dump succeeded (compose produced something
    # to serialize).
    assert composed[0].stat().st_size > 0, f"composed manifest is empty: {composed[0]}"

    # ---- Pin that we actually got past pre-flight into PTY ----
    # Same rationale as test_reflect_all_e2e.py — the session-start
    # banner is the strong proof of WrapRunner spawn.
    plain = strip_ansi(result.stdout)
    assert "Launching role-judge" in plain, (
        "reflect role did not advance to role-judge launch — "
        f"regression in _run_role_audit?\nstdout (tail 800):\n{plain[-800:]}"
    )
    assert "Session:" in plain, (
        "session-start banner missing — WrapRunner did not spawn?\n"
        f"stdout (tail 800):\n{plain[-800:]}"
    )

    # ---- Defensive: no traceback leak ----
    assert "Traceback" not in plain, (
        f"traceback leaked to user-facing output:\nstdout (tail 800):\n{plain[-800:]}"
    )
