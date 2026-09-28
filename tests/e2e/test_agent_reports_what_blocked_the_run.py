"""e2e (HATS-2047)

flow:   an orchestrator launches a claude sub-agent whose run the API refuses
        mid-run, and reads the result to decide whose move it is
cmds:
    ai-hats agent assistant --task "Reply with just: ok" --model <no such model> --json
    ai-hats agent assistant --task "Reply with just: ok" --model <no such model>
expect: exit 1; the envelope carries `blocked` (harness_must_act / abort, with
        claude's own words) and an `error` naming it; the human summary says
        "Sub-agent failed" with that line
why:    the refusal is recorded by the transcript reader inside the session
        process and folded into metrics.json at finalize, so only a real claude
        run proves the signal travels from the API's answer to the caller —
        before this, the same run printed a green "Sub-agent completed" and an
        envelope with nothing but exit_code 1
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json

import pytest

from _helpers.project import Project

pytestmark = [pytest.mark.integration, pytest.mark.observe, pytest.mark.surfaces]

# The API answers 404 before any model runs, so the probe bills nothing;
# claude records it as a `model_not_found` api-error.
MISSING_MODEL = "claude-nonexistent-model-9"
TASK_PROMPT = "Reply with just: ok"
AGENT_TIMEOUT = 90.0


def _envelope(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith("{") and '"exit_code"' in line:
            return json.loads(line)
    raise AssertionError(f"no --json envelope in stdout:\n{stdout[-800:]}")


def _run(project: Project, *extra: str):
    return project.run(
        "agent",
        "assistant",
        "--task",
        TASK_PROMPT,
        "--model",
        MISSING_MODEL,
        *extra,
        timeout=AGENT_TIMEOUT,
        extra_env={"AI_HATS_NO_UPDATE_CHECK": "1"},
    )


def test_the_envelope_says_what_blocked_the_run(
    tmp_project: Project,
    requires_claude_auth,  # noqa: ARG001 — skip-marker fixture
) -> None:
    result = _run(tmp_project, "--json")

    assert result.exit_code == 1, (
        f"exit {result.exit_code}\nstdout:\n{result.stdout[-800:]}\nstderr:\n{result.stderr[-800:]}"
    )
    envelope = _envelope(result.stdout)
    assert envelope["exit_code"] == 1
    blocked = envelope.get("blocked")
    assert blocked, f"no `blocked` in the envelope: {sorted(envelope)}"
    assert blocked["obligation"] == "harness_must_act"
    assert blocked["kind"] == "abort"
    assert blocked["source"] == "claude/jsonl"
    assert blocked["detail"], "claude's own words are missing"
    assert envelope["error"].startswith("the harness must abort"), envelope["error"]


def test_the_summary_says_the_run_failed(
    tmp_project: Project,
    requires_claude_auth,  # noqa: ARG001 — skip-marker fixture
) -> None:
    result = _run(tmp_project)

    assert result.exit_code == 1, (
        f"exit {result.exit_code}\nstdout:\n{result.stdout[-800:]}\nstderr:\n{result.stderr[-800:]}"
    )
    assert "Sub-agent failed" in result.stdout, result.stdout[-800:]
    assert "Sub-agent completed" not in result.stdout
    assert "the harness must abort" in result.stdout
