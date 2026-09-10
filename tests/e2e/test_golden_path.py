"""e2e (HATS-483)

flow:   a new user installs the launcher, initialises a project, checks the
        composed prompt, and runs one real batch turn — the canonical journey,
        end to end against a live Claude SDK
cmds:
    bash scripts/install-launcher.sh        # via the tmp_venv_project fixture
    ai-hats self init -r assistant -p claude --no-update
    ai-hats config show-prompt
    ai-hats execute --batch -r assistant -p claude --model claude-haiku-4-5 \
        --prompt "Reply with exactly: OK. No other text." --json
    ai-hats                                 # bare HITL, driven over a PTY
expect: `self init` reports the role and provider and writes default_role into
        ai-hats.yaml; `show-prompt` carries the composed role's markers; the
        batch run exits 0 and emits one JSON envelope with exit_code 0, a
        session_id and a session_dir, alongside audit.md and a trace.jsonl
        naming every pipeline step; the turn's cost stays under the $0.10 cap;
        and bare `ai-hats` surfaces its session-start and session-end banners in
        the parent's stdout through the PTY proxy
why:    every layer the product sells sits on this one path — launcher install,
        yaml parsing, role and provider validation, composition, prompt
        materialisation, the pipeline harness and both runners. Three of bare
        `ai-hats`'s four steps are byte-identical to the batch pipeline's, so a
        composition or provider regression that breaks the product breaks here.

"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _helpers.hitl import drive_bare_hitl
from ai_hats_observe.artifacts import AUDIT_MD
from ai_hats.paths import PROJECT_CONFIG


pytestmark = [pytest.mark.integration, pytest.mark.install, pytest.mark.library]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_json_envelope(stdout: str) -> dict:
    """Find the ``execute --batch --json`` envelope in ``stdout``.

    Iterates lines in reverse and returns the first that parses as a
    JSON object with an ``exit_code`` key. Tolerates trailing output
    from ``render_update_banner`` (which runs AFTER the json write in
    the pipeline order per ``library/core/pipelines/execute.yaml``).
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
        "is ``execute --batch --json`` still emitting one line via "
        "``click.echo(json.dumps(payload))`` in cli/execute.py?\n"
        f"stdout (tail 800):\n{stdout[-800:]}"
    )


# ---------------------------------------------------------------------------
# Stable smoke — production PipelineHarness via ``execute --batch``
# ---------------------------------------------------------------------------


def test_golden_path_install_init_execute_batch(
    tmp_venv_project,
    requires_claude_auth,
    tmp_path: Path,
) -> None:
    """Real CLI golden-path: ``self init`` → ``show-prompt`` →
    ``execute --batch`` → audit.md + trace.jsonl + JSON output."""

    # ---- 1. self init writes provider + composition to disk ----
    tmp_venv_project.run(
        "self",
        "init",
        "-r",
        "assistant",
        "-p",
        "claude",
        "--no-update",
        timeout=120,
    ).expect_ok().expect_stdout_contains(
        "Default role: assistant",
        "Provider: claude",
    ).expect_file(
        PROJECT_CONFIG,
        contains="default_role: assistant",
    )

    # ---- 2. show-prompt surfaces composed role markers ----
    tmp_venv_project.run(
        "config",
        "show-prompt",
    ).expect_ok().expect_stdout_contains(
        "Reliability",
        "Cleanliness",
        "Velocity",
    )

    # ---- 3. execute --batch walks the real production pipeline ----
    #
    # Same ``PipelineHarness`` + same three core steps that bare
    # ``ai-hats`` uses (only ``resolve_prompt`` is extra here; the
    # ``launch_provider`` step's inner branch differs).
    trace_path = tmp_path / "pipeline_trace.jsonl"
    result = tmp_venv_project.run(
        "execute",
        "--batch",
        "-r",
        "assistant",
        "-p",
        "claude",
        # Explicit cheapest tier — without ``--model`` the provider
        # default kicks in (sonnet on current builds), which roughly
        # 5x's the per-turn cost. Tests must be predictable on $.
        "--model",
        "claude-haiku-4-5",
        "--prompt",
        "Reply with exactly: OK. No other text.",
        "--json",
        timeout=120,
        extra_env={"AI_HATS_PIPELINE_TRACE": str(trace_path)},
    ).expect_ok()

    # ---- 4. --json output = machine-readable "final line" ----
    data = _extract_json_envelope(result.stdout)
    assert data["exit_code"] == 0, data
    assert data["session_id"], data
    session_dir = Path(data["session_dir"])

    # ---- 4a. Explicit cost cap from the JSON envelope. ----
    #
    # Defensive — actual cost on haiku-4-5 with a brief prompt is
    # ~$0.02. A runaway role config, retry loop, or a model-default
    # drift (e.g. sonnet kicked in unexpectedly) would push the cost
    # past the cap. This assertion fails loud before $ vanishes.
    cost = data.get("total_cost_usd")
    assert cost is not None, f"total_cost_usd missing from envelope: {data}"
    assert cost < 0.05, f"cost overrun: ${cost} >= $0.05 (envelope: {data})"

    # ---- 5. trace.jsonl proves the expected steps fired ----
    #
    # Independent observability — catches pipeline-DAG drift even if
    # the per-step side effects look fine.
    events = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
    step_names = [e["step"] for e in events]
    expected_steps = {
        "check_update_async",
        "compose_role",
        "resolve_prompt",
        # HATS-535: step renamed ``launch_provider`` → ``provider``;
        # the legacy id is kept as a registry alias in
        # ``pipeline/steps/__init__.py`` for back-compat with any
        # external pipeline YAML, but trace.jsonl emits the canonical
        # name.
        "provider",
        "render_update_banner",
    }
    assert expected_steps.issubset(set(step_names)), (
        f"missing pipeline steps; got {step_names}, expected superset of {expected_steps}"
    )
    errored = [e for e in events if e.get("error")]
    assert not errored, f"pipeline steps errored: {errored}"

    # ---- 6. audit.md = durable post-session record ----
    audit = (session_dir / AUDIT_MD).read_text()
    for marker in (
        "- **Role**: assistant",
        "- **Provider**: claude",
        "## Composition",
        "## Metrics",
        "**total_cost_usd**",
        "**claude_session_id**",
    ):
        assert marker in audit, (
            f"audit.md missing marker {marker!r}\n"
            f"path: {session_dir / AUDIT_MD}\n"
            f"audit tail (300):\n{audit[-300:]}"
        )


# ---------------------------------------------------------------------------
# HITL probe — bare ``ai-hats`` banner surfaces in subprocess stdout?
# ---------------------------------------------------------------------------


def test_hitl_banners_via_bare_ai_hats(
    tmp_venv_project,
    requires_claude_auth,
) -> None:
    """Bare ``ai-hats`` HITL — start/end banners survive subprocess capture.

    The driver lives at :mod:`tests.e2e._helpers.hitl`; this test only
    composes its verbs. See that module's docstring for the empirical
    workarounds (stdin payload, ANSI stripping, env allowlist).
    """
    # Set up the project (same provider/role as the smoke test).
    tmp_venv_project.run(
        "self",
        "init",
        "-r",
        "assistant",
        "-p",
        "claude",
        "--no-update",
        timeout=120,
    ).expect_ok()

    (
        drive_bare_hitl(tmp_venv_project, role="assistant")
        .expect_no_hang()
        .expect_exit_in({0, 130})  # clean /exit (0) OR Ctrl-C teardown (130)
        .expect_start_banner(role="assistant", provider="claude")
        .expect_end_banner()
    )
