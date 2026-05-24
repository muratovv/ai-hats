"""E2E: HATS-474 Phase 3 — multi-turn via ``SubAgentRunner.session``.

The whole point of moving sub-agents onto :mod:`claude_agent_sdk` was
to gain first-class multi-turn. This test drives a 3-turn arithmetic
dialog against the real SDK + real ``claude`` binary and asserts:

* context is preserved across turns (the answer in turn 3 depends on
  numbers introduced in turns 1 and 2)
* the ``claude_session_id`` is stable across turns (the SDK chains
  ``query`` calls on the same client without us doing ``--resume``
  plumbing — that's the win versus PoC-2 from HATS-473)
* aggregated ``metrics.json`` records summed ``total_cost_usd``,
  ``send_count == 3``, and a non-trivial ``num_turns_total``

Cost-capped via ``claude-haiku-4-5`` and a deterministic prompt that
asks for a single number. Expected ≤ ~$0.005 per run.

Fail-under-revert (``dev_rule_e2e_gate`` §4): the multi-turn API lives
in ``runtime.SubAgentRunner.session`` shipped in HATS-474 Phase 3.
Reverting that method makes ``runner.session`` an ``AttributeError``;
this test fails to import / collect.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.observe import SessionManager


pytestmark = pytest.mark.integration


PROBE_MODEL = "claude-haiku-4-5"


@pytest.fixture
def requires_claude_auth() -> None:
    if not shutil.which("claude"):
        pytest.skip("claude binary not in PATH")
    try:
        cp = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"claude --version failed: {exc}")
    if cp.returncode != 0:
        pytest.skip(f"claude --version exit {cp.returncode}: {cp.stderr.strip()}")


@pytest.fixture
def minimal_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"
    role_dir = lib / "roles" / "calc"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: calc\n"
        "priorities: []\n"
        "composition:\n  traits: []\n  rules: []\n  skills: []\n"
        "injection: |\n"
        "  You are a deterministic calculator. Reply with ONLY the digits of\n"
        "  the requested number — no prose, no units, no punctuation, no\n"
        "  formatting. Just the number.\n"
    )
    ProjectConfig(provider="claude", library_paths=[str(lib)]).save(
        project / "ai-hats.yaml"
    )
    asm = Assembler(project)
    asm.init()
    asm.set_role("calc", provider_name="claude")
    return project


def _extract_number(text: str) -> int | None:
    """Pull the first integer out of an assistant reply — tolerant of stray
    whitespace, code fences, or a trailing period the model sometimes adds
    despite instructions."""
    import re

    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else None


def test_subagent_session_three_turn_dialog_preserves_context(
    minimal_project: Path, requires_claude_auth
) -> None:
    """3-turn arithmetic chain: T1 introduces a number, T2 modifies it,
    T3 references the prior answer. Context must survive across turns
    for T3's number to be right."""
    from ai_hats.runtime import SubAgentRunner

    runner = SubAgentRunner(minimal_project)
    captured: dict = {}

    async def _drive():
        async with runner.session("calc", model=PROBE_MODEL) as s:
            r1 = await s.send(
                "Remember this number: 17. Reply with just the digits."
            )
            r2 = await s.send(
                "Add 8 to that number. Reply with just the digits."
            )
            r3 = await s.send(
                "Double the result from the previous turn. "
                "Reply with just the digits."
            )
            captured["r1"] = r1
            captured["r2"] = r2
            captured["r3"] = r3
            captured["session_id"] = s.session_id
            captured["claude_session_id"] = s.claude_session_id

    asyncio.run(_drive())

    n1 = _extract_number(captured["r1"].text)
    n2 = _extract_number(captured["r2"].text)
    n3 = _extract_number(captured["r3"].text)

    # T1: should be 17.
    assert n1 == 17, f"T1 reply did not echo 17: {captured['r1'].text!r}"
    # T2: 17 + 8 = 25.
    assert n2 == 25, f"T2 reply did not produce 25: {captured['r2'].text!r}"
    # T3: 25 * 2 = 50. This is the actual context-preservation assertion.
    assert n3 == 50, (
        f"T3 reply did not produce 50 — context lost across turns? "
        f"r3={captured['r3'].text!r}"
    )

    # claude_session_id is stable across turns — single client, no
    # --resume plumbing.
    assert captured["claude_session_id"], "claude_session_id missing"
    assert captured["r1"].claude_session_id == captured["claude_session_id"]
    assert captured["r3"].claude_session_id == captured["claude_session_id"]

    # Aggregated metrics.json reflects all three sends.
    sess_dir = SessionManager(minimal_project).get_session(
        captured["session_id"],
    ).session_dir
    metrics = json.loads((sess_dir / "metrics.json").read_text())
    assert metrics["exit_code"] == 0
    assert metrics["send_count"] == 3
    assert metrics["num_turns_total"] >= 3, metrics
    assert metrics["total_cost_usd"] is not None
    assert metrics["total_cost_usd"] < 0.10, metrics  # generous CI cap
    assert metrics["claude_session_id"] == captured["claude_session_id"]

    # Aggregated transcript: one section per send, all three numbers present.
    transcript = (sess_dir / "transcript.txt").read_text()
    assert "==== turn 1 ====" in transcript
    assert "==== turn 2 ====" in transcript
    assert "==== turn 3 ====" in transcript
    assert "17" in transcript and "25" in transcript and "50" in transcript
