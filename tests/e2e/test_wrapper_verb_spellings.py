"""e2e (HATS-1816)

flow:   the materialized consent wrapper is invoked through its real shim, with
        the verb spelled the ways that used to slip past it
cmds:
    ai-hats --provider claude wt merge task/x
    rack transition --tasks-dir /t HATS-1 execute
    rack transition --state execute HATS-1
expect: every spelling is RECOGNIZED as the declared operation, so the wrapper
        stops instead of spawning the original binary
why:    the wrapper read argv positionally while the PreToolUse gate skipped
        flags and their values. A flag before the positional argument therefore
        reached the wrapper as no-match and the operation ran with no question —
        and `--state execute X` matched with the ticket bound to `--state`
        instead of the task id. Revert `operations.operands` and the two
        `--provider`/`--tasks-dir` rows spawn the stub, turning these red.
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
SPAWNED = 0  # the stub ran: the wrapper did NOT recognize the operation
REFUSED = 2  # the wrapper recognized it and could not ask (no tty, no ack)


@pytest.fixture
def wrapped(tmp_path: Path):
    """A real shim over a stub binary, driven by a real wrapper config."""
    originals = tmp_path / "orig"
    originals.mkdir()
    marker = tmp_path / "spawned.txt"
    for surface in ("rack", "ai-hats"):
        stub = originals / surface
        stub.write_text(
            f"#!{sys.executable}\nimport sys\n"
            f"open({str(marker)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "project_dir": str(tmp_path),
                "originals": {s: str(originals / s) for s in ("rack", "ai-hats")},
                "policy": {
                    # Source-less arrows on purpose: this test is about FLAG parsing, and a
                    # `plan->execute` selector would also need the card to exist in `plan`
                    # (HATS-1813) — a second variable in a test that has one subject.
                    "rack.transition": ["->execute", "->done"],
                    "wt.merge": ["pre-merge"],
                },
            }
        ),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for surface in ("rack", "ai-hats"):
        shim = bin_dir / surface
        shim.write_text(
            f"#!{sys.executable}\nfrom ai_hats.consent_wrapper import main\n"
            f"raise SystemExit(main({surface!r}))\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)

    env = dict(os.environ)
    env["AI_HATS_CONSENT_WRAPPER_CONFIG"] = str(config)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "src"), str(REPO_ROOT / "packages/ai-hats-library/src")]
    )
    for ack in ("AI_HATS_CONSENT_ACK", "AI_HATS_MERGE_ACK", "AI_HATS_PLAN_ACK"):
        env.pop(ack, None)
    return bin_dir, env, marker


def _run(wrapped, surface: str, argv: list[str]) -> int:
    bin_dir, env, _marker = wrapped
    return subprocess.run(  # noqa: S603 - shim path built by the fixture
        [str(bin_dir / surface), *argv], env=env, capture_output=True, text=True, timeout=60
    ).returncode


@pytest.mark.parametrize(
    ("surface", "argv"),
    [
        ("ai-hats", ["--provider", "claude", "wt", "merge", "task/x"]),
        ("ai-hats", ["-r", "maintainer", "wt", "merge", "task/x"]),
        ("ai-hats", ["wt", "merge", "task/x"]),
        ("rack", ["transition", "--tasks-dir", "/t", "HATS-1", "execute"]),
        ("rack", ["transition", "--state", "execute", "HATS-1"]),
        ("rack", ["transition", "HATS-1", "execute"]),
    ],
)
def test_a_flag_does_not_hide_the_verb_from_the_wrapper(wrapped, surface, argv):
    """Recognized means the stub never runs — the wrapper stopped the call."""
    _bin, _env, marker = wrapped
    assert _run(wrapped, surface, argv) != SPAWNED, f"{surface} {' '.join(argv)} slipped through"
    assert not marker.exists(), f"the original binary ran for {surface} {' '.join(argv)}"


@pytest.mark.parametrize(
    ("surface", "argv"),
    [
        ("rack", ["ls"]),
        ("rack", ["transition", "HATS-1", "--log", "execute"]),
        ("ai-hats", ["wt", "list"]),
    ],
)
def test_a_call_that_is_not_the_verb_passes_through(wrapped, surface, argv):
    """The other half: a note ABOUT a move, and neighbouring verbs, still run."""
    _bin, _env, marker = wrapped
    assert _run(wrapped, surface, argv) == SPAWNED
    assert marker.exists(), f"the wrapper swallowed {surface} {' '.join(argv)}"
