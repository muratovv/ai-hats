"""e2e (HATS-1877)

flow:   a maintainer closes a card while master's own CI has been failing
cmds:
    bash scripts/gates.sh master-ci
    hooks/done-gate.sh --stages
expect: a green master passes; a red one refuses with exit 1, names the
        conclusion and the run url, and points at the one override; the
        override lets the card that fixes master through; and every reason the
        check cannot answer (no gh, gh refusing, a run still going) is
        ANNOUNCED, never silent
why:    CI had been red since before 2026-07-28 for an unrelated reason, so the
        one arm that could see seven of v0.15.0's nine defects went unread for
        a month. A skip nobody is told about is that same defect wearing the
        gate's own colours
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check_master_ci.py"

ENV_ALLOW_RED = "AI_HATS_RED_MASTER_ACK"


def _fake_gh(tmp_path: Path, *, stdout: str = "", exit_code: int = 0) -> Path:
    """A `gh` on PATH that answers with exactly what the test wants."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(parents=True, exist_ok=True)
    gh = shim_dir / "gh"
    gh.write_text(
        f"#!/usr/bin/env bash\ncat <<'JSON'\n{stdout}\nJSON\nexit {exit_code}\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return shim_dir


def _run(shim_dir: Path | None, **env_extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop(ENV_ALLOW_RED, None)
    env.update(env_extra)
    if shim_dir is None:
        env["PATH"] = ""
    else:
        env["PATH"] = f"{shim_dir}:{env.get('PATH', '')}"
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _runs(conclusion: str, status: str = "completed") -> str:
    return json.dumps(
        [
            {
                "conclusion": conclusion,
                "status": status,
                "displayTitle": "some commit subject",
                "url": "https://github.com/muratovv/ai-hats/actions/runs/1",
                "headSha": "0" * 40,
            }
        ]
    )


def test_a_green_master_passes(tmp_path: Path):
    run = _run(_fake_gh(tmp_path, stdout=_runs("success")))
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "[master-ci] ok:" in combined, combined


def test_a_red_master_refuses_and_names_the_run(tmp_path: Path):
    """The RED baseline HATS-1877 was opened over: master concluded `failure`
    for a month and no gate on the way to `done` ever asked."""
    run = _run(_fake_gh(tmp_path, stdout=_runs("failure")))
    combined = run.stdout + run.stderr
    assert run.returncode == 1, combined
    assert "failure" in combined, combined
    assert "actions/runs/1" in combined, combined
    assert ENV_ALLOW_RED in combined, combined
    assert "Fix master first" in combined, combined
    assert "v0.15.0" not in combined, "a history lesson is not a remedy"


def test_the_override_lets_the_fix_for_the_redness_through(tmp_path: Path):
    run = _run(_fake_gh(tmp_path, stdout=_runs("failure")), **{ENV_ALLOW_RED: "1"})
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "on the supervisor's word" in combined, combined


def test_a_run_still_going_is_not_a_verdict(tmp_path: Path):
    run = _run(_fake_gh(tmp_path, stdout=_runs("", status="in_progress")))
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "in_progress" in combined, combined


def test_no_gh_is_announced_not_silent():
    run = _run(None)
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "SKIPPED" in combined, combined
    assert "not on PATH" in combined, combined
    assert "was not checked" in combined, combined


def test_gh_refusing_is_announced_not_silent(tmp_path: Path):
    run = _run(_fake_gh(tmp_path, stdout="gh: not authenticated", exit_code=4))
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "SKIPPED" in combined, combined
    assert "exited 4" in combined, combined


def test_unreadable_output_is_announced_not_silent(tmp_path: Path):
    run = _run(_fake_gh(tmp_path, stdout="not json at all"))
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "SKIPPED" in combined, combined
    assert "cannot read" in combined, combined


_GATE_HOOKS = (
    "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/maintainer-quality-gate/hooks"
)


def test_the_done_gate_composition_names_the_stage():
    """The gate runs what its `--stages` names, so dropping it here disarms it."""
    listed = subprocess.run(
        ["bash", f"{_GATE_HOOKS}/done-gate.sh", "--stages"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "master-ci" in listed.stdout.split(), listed.stdout


def test_the_merge_gate_stays_offline():
    """`merge-gate` must remain runnable without network — it is the fast edge."""
    listed = subprocess.run(
        ["bash", f"{_GATE_HOOKS}/merge-gate.sh", "--stages"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert "master-ci" not in listed.stdout.split(), listed.stdout
