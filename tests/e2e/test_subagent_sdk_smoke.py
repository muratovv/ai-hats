"""e2e (HATS-474)

flow:   an agent spawning sub-agent execution via SubAgentRunner SDK integration
cmds:
    # sub-agent execution via SubAgentRunner
    ai-hats agent probe --task "Reply PONG"
expect: sub-agent runs through Claude SDK, recording cost telemetry and session ID in
        metrics.json
why:    without SDK integration, sub-agent execution relies on legacy subprocesses and
        loses cost telemetry
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats_observe.artifacts import METRICS_JSON, TRANSCRIPT_TXT
from ai_hats.paths import PROJECT_CONFIG


pytestmark = pytest.mark.integration


PROBE_MODEL = "claude-haiku-4-5"
PROBE_BUDGET_USD = 0.10
PROBE_TIMEOUT_S = 120
PROBE_TASK = "Reply with exactly the four characters: PONG. Nothing else."


@pytest.fixture
def requires_claude_auth() -> None:
    """Skip if the bundled ``claude`` binary is missing or unauthenticated.

    Mirrors ``tests/e2e/conftest.py``: ``claude --version`` is a cheap
    liveness probe that doesn't burn quota. Full auth is detected by
    the SDK itself surfacing an error during the run — we don't add a
    second probe here.
    """
    if not shutil.which("claude"):
        pytest.skip("claude binary not in PATH")
    try:
        cp = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"claude --version failed: {exc}")
    if cp.returncode != 0:
        pytest.skip(f"claude --version exit {cp.returncode}: {cp.stderr.strip()}")


@pytest.fixture
def minimal_claude_project(tmp_path: Path) -> Path:
    """A project just rich enough for ``SubAgentRunner.run('probe')``.

    No traits/skills — keeps composition cheap and the SYSTEM_ROLE
    section minimal so probe runs are deterministic and low-cost.
    """
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "-b", "master"], cwd=str(project), check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(project), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(project), check=True)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=str(project), check=True)

    lib = tmp_path / "lib"

    role_dir = lib / "roles" / "probe"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: probe\n"
        "priorities: []\n"
        "composition:\n  traits: []\n  rules: []\n  skills: []\n"
        "injection: |\n"
        "  You are a one-word echo bot. Respond with exactly what you are asked.\n"
    )

    ProjectConfig(provider="claude", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)

    asm = Assembler(project)
    asm.init()
    asm.set_role("probe", provider_name="claude")
    return project


def test_subagent_runner_via_sdk_smoke(minimal_claude_project: Path, requires_claude_auth) -> None:
    from ai_hats.composition_seam import build_composition_payload
    from ai_hats_observe import SessionManager
    from ai_hats.runtime import SubAgentRunner

    # HATS-865: compose once at the integrator seam, inject the payload.
    payload = build_composition_payload(
        minimal_claude_project,
        role_override="probe",
    )
    runner = SubAgentRunner(
        ProjectLayout.at(minimal_claude_project),
        payload,
        session_mgr=SessionManager(
            minimal_claude_project, runs_dir=ProjectLayout.at(minimal_claude_project).sessions.runs
        ),
    )

    # Inject a per-call budget via the build_options call site by
    # monkey-patching the wrapper's signature is fragile; instead we
    # rely on the SDK's max_budget_usd guard configured statically in
    # the helper. For this probe a static cap is not wired; we accept
    # the small uncapped cost (~$0.001 with haiku and a 4-char response).
    session = runner.run(
        task=PROBE_TASK,
        isolation_mode="discard",
        model=PROBE_MODEL,
    )

    metrics_path = session.session_dir / METRICS_JSON
    assert metrics_path.is_file(), "metrics.json missing — finalize did not run"
    metrics = json.loads(metrics_path.read_text())

    assert metrics["exit_code"] == 0, (
        f"sub-agent failed: error={metrics.get('error')!r} timed_out={metrics.get('timed_out')!r}"
    )
    # SDK-specific telemetry — the whole point of Phase 2.
    print("METRICS JSON:", metrics)

    assert metrics.get("claude_session_id"), (
        "claude_session_id absent from metrics.json — ResultMessage.session_id capture is broken"
    )
    assert metrics.get("total_cost_usd") is not None, (
        "total_cost_usd absent — SDK cost telemetry not threaded through"
    )
    assert metrics.get("total_cost_usd") < PROBE_BUDGET_USD, (
        f"cost {metrics['total_cost_usd']} exceeded soft budget "
        f"{PROBE_BUDGET_USD} — probe is more expensive than intended"
    )
    assert metrics.get("num_turns", 0) >= 1
    assert metrics.get("stop_reason"), "stop_reason missing from metrics.json"

    transcript = (session.session_dir / TRANSCRIPT_TXT).read_text()
    assert transcript.strip(), "transcript empty — formatter produced no output"
    # The agent was asked to reply "PONG"; allow some flexibility but
    # require recognizable echo (case-insensitive, may include
    # punctuation).
    assert "pong" in transcript.lower(), f"agent did not respond to the echo prompt: {transcript!r}"
