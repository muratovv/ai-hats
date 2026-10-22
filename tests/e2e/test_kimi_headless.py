"""e2e (HATS-2059)

flow:   ai-hats driving the real `kimi` CLI headless against a per-session
        KIMI_CODE_HOME (the shape plan_hooks materializes), then reading the
        session record it left
cmds:   kimi --output-format=stream-json -p "<prompt>"  (argv built by
        KimiSurface.get_run_command)
expect: exit 0, an assistant answer and a session.resume_hint meta line on
        stdout; wire.jsonl under the session home; KimiParser reads turns and
        token telemetry from it. With a deny [[hooks]] guard installed, a Bash
        call is refused and the model sees the reason.
why:    this is the task's core acceptance — headless first-class — and the
        parser must prove itself on a record the real harness wrote, not only
        on fixtures. Needs the kimi CLI and an OAuth login; skips everywhere
        else. Kept out of the mandatory gate (live_headless) because it
        needs that login; run it with `-m live_headless`
"""  # comment-length: allow — the e2e flow/cmds/expect/why header is the contract

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_hats.surfaces.kimi import KimiSurface
from ai_hats.surfaces.kimi.parser import KimiParser

pytestmark = [pytest.mark.integration, pytest.mark.surfaces, pytest.mark.live_headless]

KIMI = shutil.which("kimi")
# The person's REAL home, not an inherited KIMI_CODE_HOME: inside an ai-hats
# session that variable points at a session home whose config.toml already
# carries ai-hats [[hooks]] — snapshotting it would drag the parent session's
# hook chain into the "clean" home this suite builds.
_REAL_HOME = Path.home() / ".kimi-code"
_LOGGED_IN = (_REAL_HOME / "credentials").is_dir() and any(
    (_REAL_HOME / "credentials").glob("*.json")
)

needs_kimi = pytest.mark.skipif(
    KIMI is None or not _LOGGED_IN,
    reason="needs the kimi CLI with an OAuth login on this host",
)


def _session_home(tmp_path: Path, *, hooks_toml: str = "") -> Path:
    """The per-session home plan_hooks materializes: config snapshot + hooks,
    credentials symlinked back to the real home."""
    home = tmp_path / "kimi-home"
    home.mkdir()
    base = (_REAL_HOME / "config.toml").read_text(encoding="utf-8")
    (home / "config.toml").write_text(base.rstrip() + "\n\n" + hooks_toml)
    os.symlink(_REAL_HOME / "credentials", home / "credentials")
    return home


def _run_kimi(home: Path, cwd: Path, prompt: str) -> subprocess.CompletedProcess:
    argv = KimiSurface().get_run_command(["kimi"], prompt)
    # Scrub the parent ai-hats session (if any) from the child's environment —
    # only the per-session home under test may define this run.
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    env["KIMI_CODE_HOME"] = str(home)
    return subprocess.run(  # noqa: S603 — argv from the surface under test
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _stream_lines(raw: str) -> list[dict]:
    return [json.loads(line) for line in raw.splitlines() if line.strip().startswith("{")]


@needs_kimi
def test_headless_run_leaves_a_parseable_wire_record(tmp_path: Path):
    home = _session_home(tmp_path)
    proc = _run_kimi(home, tmp_path, "Reply with exactly: ok")
    assert proc.returncode == 0, proc.stderr[-500:]
    lines = _stream_lines(proc.stdout)
    assert any(
        line.get("role") == "assistant" and "ok" in str(line.get("content")) for line in lines
    )
    hint = next(
        (
            line
            for line in lines
            if line.get("role") == "meta" and line.get("type") == "session.resume_hint"
        ),
        None,
    )
    assert hint, f"no resume_hint in stream: {proc.stdout[-500:]}"

    wires = list(home.glob("sessions/*/session_*/agents/main/wire.jsonl"))
    assert len(wires) == 1, f"expected one wire.jsonl under {home}/sessions"
    parsed = KimiParser().parse(wires[0], tmp_path / "trace.log")
    assert len(parsed.turns) == 1
    assert parsed.turns[0].response.strip() == "ok"
    assert parsed.agg_usage["output_tokens"] > 0
    assert "schema-drift" not in parsed.flags


@needs_kimi
def test_pretooluse_guard_denies_bash(tmp_path: Path):
    guard = tmp_path / "guard.sh"
    guard.write_text("#!/bin/sh\necho 'e2e guard says no' >&2\nexit 2\n")
    guard.chmod(0o755)
    hooks = (
        f'[[hooks]]\nevent = "PreToolUse"\nmatcher = "Bash"\ncommand = "{guard}"\ntimeout = 10\n'
    )
    home = _session_home(tmp_path, hooks_toml=hooks)
    proc = _run_kimi(home, tmp_path, "Use the shell tool to run: echo should-not-run.")
    assert proc.returncode == 0, proc.stderr[-500:]
    lines = _stream_lines(proc.stdout)
    refused = any("e2e guard says no" in str(line.get("content")) for line in lines)
    assert refused, f"guard reason not visible in stream: {proc.stdout[-800:]}"
    # Positive control: the guard armed at all — no Bash result may carry output.
    assert not any(
        line.get("role") == "tool" and "should-not-run" in str(line.get("content"))
        for line in lines
    )


@needs_kimi
def test_role_reaches_agent_via_session_home_agents_md(tmp_path: Path):
    """The HITL role channel, verified headless: kimi injects
    $KIMI_CODE_HOME/AGENTS.md — exactly the entry the plan materializes."""
    home = _session_home(tmp_path)
    (home / "AGENTS.md").write_text(
        "# ROLE\nYou are a probe agent. When asked about your role, "
        "answer with exactly the word XYZZY.\n"
    )
    proc = _run_kimi(home, tmp_path, "What is your role? Answer in one word.")
    assert proc.returncode == 0, proc.stderr[-500:]
    lines = _stream_lines(proc.stdout)
    assert any(
        line.get("role") == "assistant" and "XYZZY" in str(line.get("content")) for line in lines
    ), f"role text did not reach the model: {proc.stdout[-500:]}"
