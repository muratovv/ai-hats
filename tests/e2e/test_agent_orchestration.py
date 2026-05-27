"""E2E: ``ai-hats agent <role> --task ... --json`` orchestration envelope.

User-scenario coverage (HATS-545 / S-CLI-04). ``ai-hats agent`` is
documented in ``docs/how-to-orchestration.md`` as the user-facing
orchestration surface — CI / ``parallel`` / ``xargs`` scripts pipe its
``--json`` stdout through ``jq`` to chain sub-agent runs. This test
pins that envelope contract so a stdout schema break is loud, not
silent.

What's asserted (the documented subset that orchestration scripts
actually consume):

- ``exit_code`` — for shell exit propagation through pipelines.
- ``session_id`` — non-empty string; ``parallel`` pipes it onward.
- ``session_dir`` — real directory on disk; downstream readers
  consume artefacts from it.
- ``total_cost_usd`` — present, float, below cost cap (cost-aware
  orchestration scripts grep / sum it).

Composition cross-check (free side-effect of having ``session_dir``):
``metrics["role"] == "assistant"`` — proves the composer wired the
role correctly through the ``ai-hats agent`` entry-point (distinct
from ``execute --batch`` which HATS-498 already guards).

We deliberately do NOT assert the FULL envelope shape: extra metrics
fields may legitimately come and go across versions. Pinning the
documented subset only.

Cost shape: ~$0.01-0.02 / run on haiku × 1 test = ~$0.02 / run.
Cost cap $0.10 (5× headroom) catches a runaway composition explosion
before $ vanishes.

Fixture choice: ``tmp_project`` (dev-venv binary) rather than
``tmp_venv_project`` (launcher-venv) because the launcher build runs
``self update`` which currently refuses to install when the worktree
branch is ahead of master (file a sibling under HATS-484 for a
worktree-aware build). ``tmp_project`` uses the dev ``ai-hats`` and
inherits the real HOME's claude auth, which is exactly what
S-CLI-04 needs for envelope-contract verification. The
``ai-hats agent`` codepath under test is the same regardless of which
venv the binary lives in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _helpers.project import Project
from _helpers.sessions import read_metrics


pytestmark = pytest.mark.integration


# Pinned to haiku — cheapest viable model. The test exercises envelope
# structure + role composition wiring, not instruction-following
# fidelity, so the smallest model is the right choice.
DRIVE_MODEL = "claude-haiku-4-5"

# Micro-prompt: one-token reply, deterministic, near-floor cost.
# Same shape as HATS-498's drive prompt — semantic content is
# irrelevant to S-CLI-04's claims.
TASK_PROMPT = "Reply with just: ok"

# Subprocess wall-clock budget. The ``agent`` invocation is dominated
# by the SDK turn (~5-10s on haiku) + ~5s harness overhead. 90s
# envelope follows the HATS-498 precedent.
AGENT_TIMEOUT = 90.0

# Cost cap: ~5× the observed cost on haiku. If this trips, something
# composition-shaped exploded — the right response is investigation,
# not raising the cap.
COST_CAP_USD = 0.10


def _extract_json_envelope(stdout: str) -> dict:
    """Find the ``ai-hats agent --json`` envelope (one-line dict with
    ``exit_code``) in ``stdout``.

    Iterates lines in reverse and returns the first that parses as a
    JSON object with an ``exit_code`` key. Tolerates surrounding
    pipeline output (``render_update_banner`` etc.) which may print
    extra lines after the JSON write. Mirror of the helper in
    ``test_role_session_retro_vertical.py`` — kept local until a third
    consumer justifies extraction to ``_helpers/`` (design-minimalism §;
    HATS-498 / HATS-545 are the two consumers so far).
    """
    for raw in reversed(stdout.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and "exit_code" in obj:
            return obj
    raise AssertionError(
        "no JSON envelope with 'exit_code' key found in stdout — "
        "is ``ai-hats agent --json`` still emitting one line via "
        "``click.echo(json.dumps(payload))`` in cli/agent.py?\n"
        f"stdout (tail 800):\n{stdout[-800:]}"
    )


def test_agent_emits_documented_json_envelope(
    tmp_project: Project,
    requires_claude_auth,  # noqa: ARG001 — skip-marker fixture
) -> None:
    """``ai-hats agent assistant --task ... --json`` emits the envelope
    documented in ``docs/how-to-orchestration.md``.

    The ``tmp_project`` fixture has already run ``Assembler.init()``
    so the ``.agent/ai-hats/`` tree exists; the ``assistant`` role
    ships in the framework's bundled library (resolves regardless of
    project ``library_paths`` being empty).
    """
    result = tmp_project.run(
        "agent", "assistant",
        "--task", TASK_PROMPT,
        "--model", DRIVE_MODEL,
        "--json",
        timeout=AGENT_TIMEOUT,
    ).expect_ok()

    envelope = _extract_json_envelope(result.stdout)

    # ----- exit_code (shell propagation surface) -----
    assert envelope["exit_code"] == 0, (
        f"envelope exit_code={envelope['exit_code']!r} (expected 0); "
        f"full envelope: {envelope}"
    )

    # ----- session_id (parallel/jq downstream key) -----
    sid = envelope.get("session_id")
    assert isinstance(sid, str) and sid, (
        f"envelope session_id missing or empty: {sid!r}"
    )

    # ----- session_dir (downstream artefact reads) -----
    sdir_raw = envelope.get("session_dir")
    assert isinstance(sdir_raw, str) and sdir_raw, (
        f"envelope session_dir missing or empty: {sdir_raw!r}"
    )
    session_dir = Path(sdir_raw)
    assert session_dir.is_dir(), (
        f"envelope session_dir is not an existing directory: {session_dir}"
    )

    # ----- total_cost_usd (cost-aware orchestration) -----
    cost = envelope.get("total_cost_usd")
    assert isinstance(cost, (int, float)), (
        f"envelope total_cost_usd missing or wrong type: {cost!r} "
        f"({type(cost).__name__})"
    )
    assert cost < COST_CAP_USD, (
        f"cost ${cost:.4f} >= cap ${COST_CAP_USD} — runaway composition?"
    )

    # ----- Composition cross-check (free with session_dir) -----
    metrics = read_metrics(session_dir)
    assert metrics["role"] == "assistant", (
        f"metrics.role={metrics['role']!r} (expected 'assistant') — "
        f"``ai-hats agent <role>`` composition wiring regression?"
    )
