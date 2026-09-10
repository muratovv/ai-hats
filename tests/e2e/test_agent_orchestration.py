"""e2e (HATS-498, HATS-545)

flow:   a developer running agent orchestration with json stdout formatting
cmds:
    ai-hats agent assistant --task "Reply with just: ok" --json
expect: the process outputs a JSON envelope containing exit_code, session_id,
        session_dir, and total_cost_usd
why:    without structured json output, orchestration pipelines cannot parse session
        metadata or propagate shell exit codes
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _helpers.project import Project
from _helpers.sessions import read_metrics


pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


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
        "agent",
        "assistant",
        "--task",
        TASK_PROMPT,
        "--model",
        DRIVE_MODEL,
        "--json",
        timeout=AGENT_TIMEOUT,
    ).expect_ok()

    envelope = _extract_json_envelope(result.stdout)

    # ----- exit_code (shell propagation surface) -----
    assert envelope["exit_code"] == 0, (
        f"envelope exit_code={envelope['exit_code']!r} (expected 0); full envelope: {envelope}"
    )

    # ----- session_id (parallel/jq downstream key) -----
    sid = envelope.get("session_id")
    assert isinstance(sid, str) and sid, f"envelope session_id missing or empty: {sid!r}"

    # ----- session_dir (downstream artefact reads) -----
    sdir_raw = envelope.get("session_dir")
    assert isinstance(sdir_raw, str) and sdir_raw, (
        f"envelope session_dir missing or empty: {sdir_raw!r}"
    )
    session_dir = Path(sdir_raw)
    assert session_dir.is_dir(), f"envelope session_dir is not an existing directory: {session_dir}"

    # ----- total_cost_usd (cost-aware orchestration) -----
    cost = envelope.get("total_cost_usd")
    assert isinstance(cost, (int, float)), (
        f"envelope total_cost_usd missing or wrong type: {cost!r} ({type(cost).__name__})"
    )
    assert cost < COST_CAP_USD, f"cost ${cost:.4f} >= cap ${COST_CAP_USD} — runaway composition?"

    # ----- Composition cross-check (free with session_dir) -----
    metrics = read_metrics(session_dir)
    assert metrics["role"] == "assistant", (
        f"metrics.role={metrics['role']!r} (expected 'assistant') — "
        f"``ai-hats agent <role>`` composition wiring regression?"
    )
