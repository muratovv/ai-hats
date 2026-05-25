"""Golden-path smoke — the canonical ai-hats user journey, end to end.

One bell-ring test that walks the full vertical stack:

  1. ``ai-hats self init -r assistant -p claude`` — provider write,
     composition materialised on disk.
  2. ``ai-hats config show-prompt`` — composition surfaces with the
     ``assistant`` role's stable priority markers
     (Reliability / Cleanliness / Velocity, defined in
     ``library/usage/roles/assistant/config.yaml``).
  3. Live SDK turn through :func:`tests.e2e._helpers.live.live_session`
     against that exact composition — confirms the SubAgentRunner →
     SDK client → bundled ``claude`` plumbing is alive end-to-end.

If a regression breaks ANY layer (launcher install, pip resolution,
Assembler, composer, provider build, MaterializeSystemPrompt,
SubAgentRunner, SDK auth) — this test goes red. The narrower
integration tests will likely stay green because they exercise each
layer in isolation; the golden-path is what catches "I broke the
whole product" before merge.

Cost shape: ~46s venv build (amortised via
``_shared_launcher_venv``) + ~5-10s self init + ~10-20s SDK turn.
Capped at $0.10 per turn (haiku-4-5, brief instruction, brief
expected reply — actual cost is typically <$0.02).

Skip behaviour:

* No ``claude`` binary on PATH → ``requires_claude_auth`` skips.
* No network / no warm pip cache for the launcher venv build →
  ``tmp_venv_project`` skips the module.

Verification of plan-stage assumptions (decisions §2):
``Reliability`` / ``Cleanliness`` / ``Velocity`` are the literal
``priorities:`` entries in the assistant role's ``config.yaml`` and
are rendered verbatim into the materialised system prompt by the
composer — confirmed via static grep of the library tree before
the test was added.
"""

from __future__ import annotations

import asyncio

import pytest

from _helpers.live import live_session


pytestmark = pytest.mark.integration


def test_golden_path_install_init_compose_live(
    tmp_venv_project, requires_claude_auth,
) -> None:
    # ---- 1. self init writes provider + composition to disk ----
    tmp_venv_project.run(
        "self", "init", "-r", "assistant", "-p", "claude",
        "--no-update", timeout=120,
    ).expect_ok().expect_file(
        "ai-hats.yaml", contains="default_role: assistant",
    )

    # ---- 2. show-prompt surfaces composed role markers ----
    tmp_venv_project.run(
        "config", "show-prompt",
    ).expect_ok().expect_stdout_contains(
        "Reliability", "Cleanliness", "Velocity",
    )

    # ---- 3. live SDK turn against the composed role completes ----
    async def _drive() -> None:
        async with live_session(
            tmp_venv_project.path,
            role="assistant",
            max_budget_usd=0.10,
        ) as s:
            r = await s.send(
                "Reply with exactly the two characters: OK. "
                "No other text."
            )
            r.expect_no_error().expect_contains_ci("ok")
            s.expect_claude_session_id().expect_cost_under(0.10)

    asyncio.run(_drive())
