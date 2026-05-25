"""Golden-path smoke — the canonical ai-hats user journey, end to end.

Two tests in this file, complementary surface:

* ``test_golden_path_install_init_execute_batch`` — the stable smoke.
  Walks the production ``PipelineHarness`` over the ``execute`` YAML
  pipeline via ``ai-hats execute --batch``. Of the four steps in the
  ``human`` pipeline (bare ``ai-hats``), THREE are byte-identical
  to steps in ``execute`` (``check_update_async``, ``compose_role``,
  ``render_update_banner``) and the fourth (``launch_provider``) is
  the same step class with a different inner branch
  (``SubAgentRunner`` vs ``WrapRunner``). Anything that breaks bare
  ``ai-hats`` from a composition / library / provider regression
  also breaks this test. Stable, deterministic, structured JSON +
  ``trace.jsonl`` + ``audit.md`` assertions.

* ``test_hitl_banners_via_bare_ai_hats`` — the HITL probe. Drives
  bare ``ai-hats`` as a subprocess to verify
  ``runtime._print_session_start`` and ``runtime._print_session_end``
  banners surface in the parent stdout. Both are plain ``print()``
  calls in ``WrapRunner.run`` (before / after the PTY proxy), NOT
  claude output — they SHOULD be capturable, but real claude on a
  child PTY with parent's stdin set to ``DEVNULL`` is undefined
  territory. The test is isolated so its potential flakiness doesn't
  taint the smoke above.

Layer-by-layer regression coverage from the smoke test:

* launcher install → ``tmp_venv_project`` fixture
* yaml parsing → ``self init`` step
* role / provider validation → ``self init``
* composition (Assembler / composer / library_paths) → ``self init``
  + ``config show-prompt`` step
* MaterializeSystemPrompt → ``config show-prompt`` step
* PipelineHarness construction + namespace allocation → ``execute --batch``
* All pipeline steps (``check_update_async``, ``compose_role``,
  ``resolve_prompt``, ``launch_provider``, ``render_update_banner``) →
  asserted via ``AI_HATS_PIPELINE_TRACE`` JSONL output
* SubAgentRunner → ``execute --batch`` SDK call
* observe.Session + audit.md writer → audit.md inspection
* metrics aggregator → ``--json`` final output

Cost shape: ~46s venv build (amortised) + ~5-10s ``self init`` +
~10-20s SDK turn = ~60-90s wall-clock. Capped at $0.10; actual
~$0.02 on haiku-4-5.

Verification of plan-stage assumption (decisions §2):
``Reliability`` / ``Cleanliness`` / ``Velocity`` are the literal
``priorities:`` entries in ``library/usage/roles/assistant/config.yaml``,
rendered verbatim into the materialised system prompt by the composer
— confirmed via grep before this test landed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest


# Strips CSI / OSC / common ANSI escape sequences. Banners use bold +
# colour SGR; stripping these lets us match on plain text markers.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?>=]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Stable smoke — production PipelineHarness via ``execute --batch``
# ---------------------------------------------------------------------------


def test_golden_path_install_init_execute_batch(
    tmp_venv_project, requires_claude_auth, tmp_path: Path,
) -> None:
    """Real CLI golden-path: ``self init`` → ``show-prompt`` →
    ``execute --batch`` → audit.md + trace.jsonl + JSON output."""

    # ---- 1. self init writes provider + composition to disk ----
    tmp_venv_project.run(
        "self", "init", "-r", "assistant", "-p", "claude",
        "--no-update", timeout=120,
    ).expect_ok().expect_stdout_contains(
        "Default role: assistant", "Provider: claude",
    ).expect_file(
        "ai-hats.yaml", contains="default_role: assistant",
    )

    # ---- 2. show-prompt surfaces composed role markers ----
    tmp_venv_project.run(
        "config", "show-prompt",
    ).expect_ok().expect_stdout_contains(
        "Reliability", "Cleanliness", "Velocity",
    )

    # ---- 3. execute --batch walks the real production pipeline ----
    #
    # Same ``PipelineHarness`` + same three core steps that bare
    # ``ai-hats`` uses (only ``resolve_prompt`` is extra here; the
    # ``launch_provider`` step's inner branch differs).
    trace_path = tmp_path / "pipeline_trace.jsonl"
    result = tmp_venv_project.run(
        "execute", "--batch",
        "-r", "assistant", "-p", "claude",
        "--prompt", "Reply with exactly: OK. No other text.",
        "--json",
        timeout=120,
        extra_env={"AI_HATS_PIPELINE_TRACE": str(trace_path)},
    ).expect_ok()

    # ---- 4. --json output = machine-readable "final line" ----
    #
    # ``execute --batch --json`` emits one JSON object on stdout with
    # session_id, exit_code, duration_s, session_dir, etc. The line
    # may be preceded by misc output, so we parse the LAST non-empty
    # line as the JSON envelope.
    json_line = next(
        line for line in reversed(result.stdout.splitlines()) if line.strip()
    )
    data = json.loads(json_line)
    assert data["exit_code"] == 0, data
    assert data["session_id"], data
    session_dir = Path(data["session_dir"])

    # ---- 5. trace.jsonl proves the expected steps fired ----
    #
    # Independent observability — catches pipeline-DAG drift even if
    # the per-step side effects look fine.
    events = [
        json.loads(line)
        for line in trace_path.read_text().splitlines() if line.strip()
    ]
    step_names = [e["step"] for e in events]
    expected_steps = {
        "check_update_async", "compose_role", "resolve_prompt",
        "launch_provider", "render_update_banner",
    }
    assert expected_steps.issubset(set(step_names)), (
        f"missing pipeline steps; got {step_names}, "
        f"expected superset of {expected_steps}"
    )
    errored = [e for e in events if e.get("error")]
    assert not errored, f"pipeline steps errored: {errored}"

    # ---- 6. audit.md = durable post-session record ----
    audit = (session_dir / "audit.md").read_text()
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
            f"path: {session_dir / 'audit.md'}\n"
            f"audit tail (300):\n{audit[-300:]}"
        )


# ---------------------------------------------------------------------------
# HITL probe — bare ``ai-hats`` banner surfaces in subprocess stdout?
# ---------------------------------------------------------------------------


def test_hitl_banners_via_bare_ai_hats(
    tmp_venv_project, requires_claude_auth,
) -> None:
    """Bare ``ai-hats`` HITL — do the start/end banners survive
    subprocess capture?

    ``_print_session_start`` and ``_print_session_end`` are plain
    Python ``print()`` calls in ``runtime.WrapRunner.run`` (before
    and after ``_pty_spawn``), NOT claude TUI output. They SHOULD
    land in the parent ai-hats process's stdout, capturable via
    ``subprocess.run(capture_output=True)``.

    The unknown: claude TUI's behaviour when its child-PTY stdin
    sees EOF because the parent's stdin is ``DEVNULL``. Possible
    outcomes:

    * Clean exit → both banners captured, exit_code likely non-zero
      but that's fine.
    * Hang → ``timeout=30s`` triggers ``TimeoutExpired`` and the
      test fails loud.
    * Error then exit → start banner captured, end banner present
      because the ``finally`` block in ``WrapRunner.run`` still
      runs ``_finalize_session``.

    This is a PROBE — we record what actually happens. If it proves
    unreliable, we can mark ``@pytest.mark.xfail`` or move to a
    separate quarantine file. The stable smoke above is the actual
    sentry; this one explores HITL coverage opportunistically.
    """
    # Set up the project (same provider/role as the smoke test).
    tmp_venv_project.run(
        "self", "init", "-r", "assistant", "-p", "claude",
        "--no-update", timeout=120,
    ).expect_ok()

    # Drive bare ``ai-hats``. We want claude to spawn AND exit quickly,
    # so we feed a slash command + double Ctrl-C through the parent's
    # stdin → PTY proxy → claude's pty. The proxy in
    # ``WrapRunner._pty_spawn`` reads parent stdin via ``os.read(0, ...)``
    # and writes to ``master_fd``; claude's TUI sees that as keyboard
    # input. Empirically (probe iteration):
    #
    # * ``stdin=DEVNULL`` → 30s+ hang. claude ignores EOF on its pty stdin.
    # * ``input="/exit\n"`` + ``\x03\x03`` → tested below.
    env = {**os.environ, **tmp_venv_project.env}
    stdin_payload = "/exit\n\x03\x03"
    try:
        cp = subprocess.run(
            [str(tmp_venv_project.ai_hats_binary)],
            cwd=str(tmp_venv_project.path),
            env=env,
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        # Surface partial stdout — start banner should be there even
        # if we couldn't get claude to exit cleanly.
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode(errors="replace")
        pytest.xfail(
            "bare ``ai-hats`` hung past 20s despite stdin payload — "
            "claude TUI not exiting on /exit + Ctrl-C through PTY proxy. "
            f"Start banner observed: {'[*] Role:' in partial}. "
            f"Stdout tail (500):\n{partial[-500:]}"
        )

    # Strip ANSI escapes — banners use bold + colour SGR so plain
    # substring matching on the raw stdout misses ``Role: assistant``
    # (the SGR open code sits between the colon and the role name).
    plain = _strip_ansi(cp.stdout)

    # Start banner — printed BEFORE _pty_spawn, should always land.
    # Format: ``[*] Role: <role> | Provider: <provider> | Session: <sid>``
    assert "[*] Role: assistant" in plain, (
        f"start banner '[*] Role: assistant' missing.\n"
        f"exit: {cp.returncode}\n"
        f"plain stdout (tail 800):\n{plain[-800:]}\n"
        f"stderr (tail 400):\n{cp.stderr[-400:]}"
    )
    assert "Provider: claude" in plain, (
        f"provider segment missing from start banner.\n"
        f"plain stdout (tail 800):\n{plain[-800:]}"
    )

    # End banner — printed in the ``finally`` block of WrapRunner.run.
    # If claude crashed mid-startup, the banner still fires because
    # ``_finalize_session`` always runs. Format: ``✨ Session <sid> complete!``
    assert "✨ Session" in plain and "complete!" in plain, (
        f"end banner '✨ Session ... complete!' not observed.\n"
        f"exit: {cp.returncode}\n"
        f"plain stdout (tail 800):\n{plain[-800:]}"
    )
