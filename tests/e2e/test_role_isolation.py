"""E2E: HATS-294 role-isolation — F5/F6 fix verification (Phase 5).

Validates the user-visible contract for HATS-294: under the unified
per-session compose path, `claude --print` reads:

- ``--system-prompt-file <cache>/prompt.md`` — the composed role's prompt
- auto-discovered ``./CLAUDE.md`` → ``imports.md`` → ``user-rules/*``

After Phase 2, ``imports.md`` no longer references priorities / role /
traits / rules / skills_index — so the agent CANNOT double-load role
content via CLAUDE.md auto-discovery (Phase 0 evidence of F5+F6).

Per ``dev_rule_e2e_gate``: real ``claude`` binary, real subprocess chain,
``@pytest.mark.integration``. Cost-capped via ``--model claude-haiku-4-5``;
expected ≤ ~$0.05 per call, ~$0.15 for the whole file.

Skip conditions:
- ``claude`` binary not in PATH
- agent reports "Not logged in" — claude CLI is not authenticated

Fail-under-revert (dev_rule_e2e_gate §4): the tests rely on
``Provider.build_session_prompt`` + ``write_canonical`` shipped in
Phase 1+2. Reverting either commit makes ``--role judge`` leak the
default-role priorities/role into the probe output and the assertions
fail.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.providers import ClaudeProvider


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROBE_MODEL = "claude-haiku-4-5"
PROBE_TIMEOUT_S = 120

# Probe modeled on the Phase 0 baseline at /tmp/hats-294-baseline/. Asks the
# agent to enumerate ROLE headings, priority lists, and visibility flags so
# the test can grep the structured output (regardless of any natural-language
# preamble).
PROBE_PROMPT = """\
Reply in EXACTLY this format and nothing else:

ROLE_HEADINGS_SEEN: <comma-separated list of every line in your loaded
system context that starts with "# ROLE:" (or "ROLE:"), case-insensitive>

PRIORITIES_LISTS_SEEN: <every distinct numbered priorities list visible to
you, joined by " || " between lists, items by ", " within a list>

PRIMARY_ASSISTANT_VISIBLE: <YES or NO — whether the substring "PRIMARY
AUTOMATION ASSISTANT" appears anywhere in your loaded system context>

JUDGE_VISIBLE: <YES or NO — whether the substring "ROLE: JUDGE" appears
anywhere in your loaded system context>

RELIABILITY_VISIBLE: <YES or NO — whether the priorities list "Reliability,
Cleanliness, Velocity" appears anywhere in your loaded system context>

DECISIVENESS_VISIBLE: <YES or NO — whether the priorities list starting with
"Decisiveness" appears anywhere in your loaded system context>
"""


# --------------------------------------------------------------------- #
# Skip helpers
# --------------------------------------------------------------------- #


def _claude_available() -> bool:
    return shutil.which("claude") is not None


def _claude_authenticated() -> bool:
    """Cheap probe: claude rejects unauthenticated --print with stderr/stdout
    containing 'Not logged in'. We do a 1-token probe to check.
    """
    if not _claude_available():
        return False
    try:
        r = subprocess.run(
            ["claude", "--print", "--model", PROBE_MODEL, "-p", "."],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return False
    blob = (r.stdout + r.stderr).lower()
    return "not logged in" not in blob and "please run /login" not in blob


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def auth_gate():
    if not _claude_available():
        pytest.skip("claude binary not found in PATH")
    if not _claude_authenticated():
        pytest.skip("claude CLI not authenticated (run /login)")


# --------------------------------------------------------------------- #
# Project fixtures
# --------------------------------------------------------------------- #


@pytest.fixture
def project_with_assistant_default(tmp_path):
    """Fresh project with the real ai-hats core library and assistant as the
    active role. CLAUDE.md is materialized (so auto-discovery has something
    to find), imports.md is the post-HATS-294 user-rules-only aggregator.
    """
    project = tmp_path / "proj"
    project.mkdir()
    lib = REPO_ROOT / "library"
    ProjectConfig(
        provider="claude",
        library_paths=[str(lib)],
        ai_hats_dir=".agent/ai-hats",
    ).save(project / "ai-hats.yaml")
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("assistant", provider_name="claude")
    return project


# --------------------------------------------------------------------- #
# Probe machinery
# --------------------------------------------------------------------- #


def _run_probe(
    project: Path,
    role: str,
    session_id: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Compose ``role``, build the session prompt, and run claude --print.

    Returns the parsed probe output as ``{KEY: value}``. Raises
    AssertionError on non-zero exit, empty output, or missing keys.
    """
    asm = Assembler(project)
    result = asm.composer.compose(role, overlay=asm._get_overlay(role))
    provider = ClaudeProvider()
    args, env, _ = provider.build_session_prompt(project, result, session_id)

    cmd = [
        "claude", "--print", "--model", PROBE_MODEL, "-p", PROBE_PROMPT, *args,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(project),
        capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
        env={**os.environ, **env, **(extra_env or {})},
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"claude probe exited {proc.returncode}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    if not proc.stdout.strip():
        raise AssertionError(f"empty stdout; stderr:\n{proc.stderr}")
    return _parse_probe(proc.stdout)


def _parse_probe(text: str) -> dict[str, str]:
    """Parse the probe's structured output into a dict.

    Tolerant of stray prose: pulls each KEY: VALUE line from anywhere in the
    output. Missing keys raise AssertionError so the test fails loudly with
    the raw response.
    """
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        for key in (
            "ROLE_HEADINGS_SEEN",
            "PRIORITIES_LISTS_SEEN",
            "PRIMARY_ASSISTANT_VISIBLE",
            "JUDGE_VISIBLE",
            "RELIABILITY_VISIBLE",
            "DECISIVENESS_VISIBLE",
        ):
            if line.strip().startswith(f"{key}:"):
                parsed[key] = line.split(":", 1)[1].strip()
                break
    required = {
        "PRIMARY_ASSISTANT_VISIBLE",
        "JUDGE_VISIBLE",
        "RELIABILITY_VISIBLE",
        "DECISIVENESS_VISIBLE",
    }
    missing = required - parsed.keys()
    if missing:
        raise AssertionError(
            f"probe response missing keys {sorted(missing)}\n"
            f"raw response:\n{text}"
        )
    return parsed


# --------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------- #


def test_role_override_no_default_leak(auth_gate, project_with_assistant_default):
    """``--role judge`` → agent sees ONLY judge content. F5+F6 must NOT
    reproduce: no PRIMARY ASSISTANT block, no Reliability priorities list.
    """
    out = _run_probe(project_with_assistant_default, "judge", "e2e-judge-only")
    assert out["JUDGE_VISIBLE"].upper() == "YES", f"judge role missing: {out}"
    assert out["DECISIVENESS_VISIBLE"].upper() == "YES", (
        f"judge priorities missing: {out}"
    )
    # F5: no double ROLE block.
    assert out["PRIMARY_ASSISTANT_VISIBLE"].upper() == "NO", (
        f"F5 regression — PRIMARY ASSISTANT leaked into --role judge: {out}"
    )
    # F6: no default-role priorities leak.
    assert out["RELIABILITY_VISIBLE"].upper() == "NO", (
        f"F6 regression — Reliability priorities leaked into --role judge: {out}"
    )


def test_default_role_session_sees_default_role(
    auth_gate, project_with_assistant_default,
):
    """Default-role session (no --role override) → agent sees the assistant
    role's PRIMARY ASSISTANT block and Reliability priorities, no judge.
    """
    out = _run_probe(project_with_assistant_default, "assistant", "e2e-default")
    assert out["PRIMARY_ASSISTANT_VISIBLE"].upper() == "YES", (
        f"default role content missing: {out}"
    )
    assert out["RELIABILITY_VISIBLE"].upper() == "YES", (
        f"default priorities missing: {out}"
    )
    assert out["JUDGE_VISIBLE"].upper() == "NO", (
        f"judge content leaked into default session: {out}"
    )
    assert out["DECISIVENESS_VISIBLE"].upper() == "NO", (
        f"judge priorities leaked into default session: {out}"
    )


def test_parallel_different_roles_isolated_with_barrier(
    auth_gate, project_with_assistant_default, tmp_path,
):
    """Two concurrent ``--role`` invocations (judge + assistant) must each
    see only their own role. Barrier file synchronizes the start so both
    processes overlap in lifetime (real concurrency proves no shared state).
    """
    project = project_with_assistant_default
    barrier = tmp_path / "barrier"
    barrier.mkdir()

    def worker(role: str, sid: str) -> tuple[str, dict[str, str], float, float]:
        # Barrier: announce, wait until both peers have announced.
        (barrier / f"{sid}.start").write_text(str(time.time()))
        deadline = time.time() + 20
        while len(list(barrier.glob("*.start"))) < 2 and time.time() < deadline:
            time.sleep(0.05)
        t0 = time.time()
        out = _run_probe(project, role, sid)
        t1 = time.time()
        return role, out, t0, t1

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(worker, "judge", "e2e-par-judge")
        fut_b = pool.submit(worker, "assistant", "e2e-par-assistant")
        result_a = fut_a.result()
        result_b = fut_b.result()

    role_a, out_a, t0_a, t1_a = result_a
    role_b, out_b, t0_b, t1_b = result_b

    # Real concurrency: lifetimes must overlap.
    assert min(t1_a, t1_b) > max(t0_a, t0_b), (
        f"sessions did not overlap: A=[{t0_a:.1f},{t1_a:.1f}] "
        f"B=[{t0_b:.1f},{t1_b:.1f}]"
    )

    # Judge session sees ONLY judge.
    assert out_a["JUDGE_VISIBLE"].upper() == "YES"
    assert out_a["PRIMARY_ASSISTANT_VISIBLE"].upper() == "NO", (
        f"cross-leak in parallel judge session: {out_a}"
    )
    assert out_a["RELIABILITY_VISIBLE"].upper() == "NO"

    # Assistant session sees ONLY assistant.
    assert out_b["PRIMARY_ASSISTANT_VISIBLE"].upper() == "YES"
    assert out_b["JUDGE_VISIBLE"].upper() == "NO", (
        f"cross-leak in parallel assistant session: {out_b}"
    )
    assert out_b["DECISIVENESS_VISIBLE"].upper() == "NO"

    # Each session got a distinct cache dir.
    from ai_hats.paths import session_cache_dir
    cache_a = session_cache_dir(project, "e2e-par-judge")
    cache_b = session_cache_dir(project, "e2e-par-assistant")
    assert cache_a != cache_b
