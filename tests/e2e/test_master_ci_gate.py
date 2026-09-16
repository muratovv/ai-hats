"""e2e (HATS-1877, HATS-1991)

flow:   a maintainer reads master's own CI verdict: by hand as a stage, and as
        the notice the push road prints before a push to master
cmds:
    bash scripts/gates.sh master-ci
    python scripts/check_master_ci.py --notice
    hooks/done-gate.sh --stages
expect: the stage passes a green master and refuses a red one with exit 1,
        naming the conclusion and the run url; the notice reports the same
        verdict and exits 0 whatever it is; every reason the check cannot
        answer (no gh, gh refusing, a run still going) is ANNOUNCED, never
        silent; and no card gate asks for the stage
why:    CI had been red since before 2026-07-28 for an unrelated reason, so the
        one arm that could see seven of v0.15.0's nine defects went unread for
        a month. A finished card then sat in review behind a red run it had no
        part in: the base's verdict is the push road's question, not a card's
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gates]

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check_master_ci.py"


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


def _run(shim_dir: Path | None, *, notice: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if shim_dir is None:
        env["PATH"] = ""
    else:
        env["PATH"] = f"{shim_dir}:{env.get('PATH', '')}"
    return subprocess.run(
        [sys.executable, str(CHECKER), *(["--notice"] if notice else [])],
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
    assert "Fix master first" in combined, combined
    assert "v0.15.0" not in combined, "a history lesson is not a remedy"
    assert "_ACK" not in combined, "there is no flag: a red base is nobody's to wave through"


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


@pytest.mark.parametrize(
    ("label", "gh", "announced"),
    [
        ("a green master", {"stdout": _runs("success")}, "ok:"),
        ("a red master", {"stdout": _runs("failure")}, "actions/runs/1"),
        ("a run still going", {"stdout": _runs("", status="in_progress")}, "in_progress"),
        ("gh refusing", {"stdout": "gh: not authenticated", "exit_code": 4}, "exited 4"),
        ("unreadable output", {"stdout": "not json at all"}, "cannot read"),
    ],
)
def test_the_notice_announces_every_outcome_and_never_refuses(
    tmp_path: Path, label: str, gh: dict, announced: str
):
    """The push road: a push is how master gets fixed, so a red base is said,
    not held against it."""
    run = _run(_fake_gh(tmp_path, **gh), notice=True)
    combined = run.stdout + run.stderr
    assert run.returncode == 0, f"{label}: {combined}"
    assert announced in combined, f"{label}: {combined}"
    assert "Fix master first" not in combined, f"{label}: a notice gives no orders"


def test_the_notice_without_gh_is_announced_not_silent():
    run = _run(None, notice=True)
    combined = run.stdout + run.stderr
    assert run.returncode == 0, combined
    assert "was not checked" in combined, combined


def test_the_notice_on_a_red_master_says_why_it_does_not_refuse(tmp_path: Path):
    run = _run(_fake_gh(tmp_path, stdout=_runs("failure")), notice=True)
    combined = run.stdout + run.stderr
    assert "failure" in combined, combined
    assert "re-runs it" in combined, combined


_GATE_HOOKS = "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate/hooks"


def test_the_done_gate_stays_offline():
    """A card can neither earn nor fix master's verdict, so `->done` does not ask
    for it — a finished card sat in review behind a red run it had no part in."""
    listed = subprocess.run(
        ["bash", f"{_GATE_HOOKS}/done-gate.sh", "--stages"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    stages = listed.stdout.split()
    assert "master-ci" not in stages, listed.stdout
    assert "integration" in stages, "positive control: the composition is being read"


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
