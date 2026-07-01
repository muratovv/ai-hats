"""E2E: ``ai-hats reflect role <name>`` materializes + launches the judge.

User-way (HATS-546 / S-CLI-29)
------------------------------

I want a coherence audit on the ``maintainer`` role — does its
trait/rule/skill composition still make sense against my project's
CLAUDE.md? I type:

    ai-hats reflect role maintainer

Pre-flight (Python) composes the target role and materializes its
layered breakdown to a per-session namespace under
``<ai_hats_dir>/sessions/runs/pipeline_runs/reflect-role/<sid>/composed/maintainer/``
(``manifest.yaml`` + ``traits/``, ``rules/``, ``skills/`` subdirs +
``role-injection.md`` if non-empty). Then the ``judge-for-role``
role launches interactively; I chat with it about the role's
coherence and it writes the audit report under
``.agent/retrospectives/role-coherence/<UTC-ts>-maintainer.md`` via
the Write tool.

What this test pins (pre-flight + launch sanity)
------------------------------------------------

1. The invocation runs to PTY without crashing.
2. The Python pre-flight (``_materialize_target_composition``)
   produced the composed-role dir with at least ``manifest.yaml`` —
   proves the role was resolved + composed + serialized to disk.
3. The PTY session-start banner appears in stdout — proves the
   ``judge-for-role`` runner spawned.
4. The PTY session exits cleanly (``{0, 130}``).
5. No Python ``Traceback`` leaks to user-facing output.

What this test does NOT pin
---------------------------

- The audit report contents. The report is LLM-written via the
  ``Write`` tool during the chat; asserting its contents requires a
  multi-turn HITL dialogue — HATS-544 territory.
- The composed manifest's payload beyond "non-empty". The
  composition correctness is HATS-498's territory; here we only
  verify that the ``reflect role`` codepath ran the materialization
  step successfully.

Fixture choice: ``tmp_project`` (dev venv) + real HOME claude auth.
Same rationale as ``test_reflect_issue_e2e.py``.

Deliberate long e2e scenario contract — noqa: comment-length.
"""

from __future__ import annotations

import pytest

from _helpers.hitl import drive_bare_hitl, strip_ansi
from _helpers.project import Project


pytestmark = pytest.mark.integration


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
    result = drive_bare_hitl(
        tmp_project,
        subcommand_args=("reflect", "role", TARGET_ROLE),
        timeout=REFLECT_ROLE_TIMEOUT,
    ).expect_no_hang().expect_exit_in({0, 130})

    # ---- Pre-flight materialization (deterministic, Python-side) ----
    # Path contract from PipelineHarness.namespace:
    #   <ai_hats_dir>/sessions/runs/pipeline_runs/<pipeline>/<session_id>/
    # plus reflect.py's ``h.namespace / "composed" / target_role``.
    pipeline_runs = (
        tmp_project.agent_dir
        / "sessions" / "runs" / "pipeline_runs" / "reflect-role"
    )
    composed = list(
        pipeline_runs.glob(f"*/composed/{TARGET_ROLE}/manifest.yaml")
    )
    assert len(composed) == 1, (
        f"expected exactly one composed manifest under {pipeline_runs}, "
        f"got {len(composed)}: {composed}"
    )
    # Manifest is a YAML dict written by _materialize_target_composition;
    # non-zero size means the dump succeeded (compose produced something
    # to serialize).
    assert composed[0].stat().st_size > 0, (
        f"composed manifest is empty: {composed[0]}"
    )

    # ---- Pin that we actually got past pre-flight into PTY ----
    # Same rationale as test_reflect_all_e2e.py — the session-start
    # banner is the strong proof of WrapRunner spawn.
    plain = strip_ansi(result.stdout)
    assert "Launching judge-for-role" in plain, (
        "reflect role did not advance to judge-for-role launch — "
        f"regression in _run_role_audit?\nstdout (tail 800):\n{plain[-800:]}"
    )
    assert "Session:" in plain, (
        "session-start banner missing — WrapRunner did not spawn?\n"
        f"stdout (tail 800):\n{plain[-800:]}"
    )

    # ---- Defensive: no traceback leak ----
    assert "Traceback" not in plain, (
        f"traceback leaked to user-facing output:\n"
        f"stdout (tail 800):\n{plain[-800:]}"
    )
