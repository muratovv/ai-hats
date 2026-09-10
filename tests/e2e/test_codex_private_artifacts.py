"""e2e (HATS-1688)

flow:   Codex HITL and Automate sessions finish under a permissive umask
cmds:
    ai-hats -p codex -r maintainer
    ai-hats execute --batch -p codex -r maintainer --isolation discard --prompt ping
expect: each session directory is 0700 and every sensitive artifact produced is 0600
why:    prompts, transcripts, traces, and audit metadata must not inherit process umask
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath, clean_env
from _helpers.git import git, init_repo
from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.runs_retention import DIAGNOSTICS_JSON
from ai_hats_observe.artifacts import (
    AUDIT_MD,
    META_PROMPT_TXT,
    METRICS_JSON,
    PTY_RAW_LOG,
    REASONING_LOG,
    ROLE_MATERIALIZATION_JSON,
    TRACE_LOG,
    TRANSCRIPT_JSONL,
    TRANSCRIPT_TXT,
    USAGE_JSON,
)

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
SENSITIVE_ARTIFACTS = (
    AUDIT_MD,
    DIAGNOSTICS_JSON,
    META_PROMPT_TXT,
    METRICS_JSON,
    PTY_RAW_LOG,
    REASONING_LOG,
    ROLE_MATERIALIZATION_JSON,
    TRACE_LOG,
    TRANSCRIPT_JSONL,
    TRANSCRIPT_TXT,
    USAGE_JSON,
)
CORE_ARTIFACTS = (
    AUDIT_MD,
    DIAGNOSTICS_JSON,
    META_PROMPT_TXT,
    METRICS_JSON,
    ROLE_MATERIALIZATION_JSON,
    TRACE_LOG,
)

_FAKE_CODEX = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import sys
import time

if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.147.0")
    raise SystemExit(0)
if sys.argv[1:] == ["login", "status"]:
    print("Logged in")
    raise SystemExit(0)

mode = os.environ["AI_HATS_TEST_CODEX_MODE"]
kind = "automate" if "exec" in sys.argv else "hitl"
session_id = os.environ["AI_HATS_SESSION_ID"]
capture_dir = Path(os.environ["AI_HATS_TEST_CAPTURE_DIR"])
capture_dir.mkdir(parents=True, exist_ok=True)
(capture_dir / f"{kind}-{mode}-{session_id}.json").write_text(
    json.dumps({"kind": kind, "mode": mode, "session_id": session_id})
)

if mode == "interrupt":
    signal.signal(signal.SIGINT, lambda *_args: sys.exit(130))
    print("codex ready", flush=True)
    while True:
        time.sleep(1)

if kind == "automate":
    print(json.dumps({"response": "sensitive automate transcript"}))
else:
    print("sensitive HITL trace")
if mode == "error":
    print("sensitive provider reasoning", file=sys.stderr)
    raise SystemExit(7)
"""


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _assert_private_session(session_dir: Path, *, required: tuple[str, ...]) -> None:
    assert _mode(session_dir) == 0o700
    missing = [name for name in required if not (session_dir / name).is_file()]
    assert not missing, f"missing session artifacts: {missing}"
    produced = [session_dir / name for name in SENSITIVE_ARTIFACTS if (session_dir / name).exists()]
    wrong = {path.name: oct(_mode(path)) for path in produced if _mode(path) != 0o600}
    assert not wrong, f"non-private session artifacts: {wrong}"


def _run(
    ai_hats_shim: Path,
    project: Path,
    env: dict[str, str],
    capture_dir: Path,
    *,
    kind: str,
    mode: str,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    before = set(capture_dir.glob("*.json"))
    argv = [str(ai_hats_shim), "-p", "codex", "-r", "maintainer"]
    stdin = ""
    if kind == "automate":
        argv = [
            str(ai_hats_shim),
            "execute",
            "--batch",
            "-p",
            "codex",
            "-r",
            "maintainer",
            "--isolation",
            "discard",
            "--prompt",
            "ping",
            "--json",
        ]
    elif mode == "interrupt":
        stdin = "\x03\x03\x03"

    child_env = {**env, "AI_HATS_TEST_CODEX_MODE": mode}
    if mode == "interrupt":
        process = subprocess.Popen(
            argv,
            cwd=project,
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            umask=0o022,
        )
        deadline = time.monotonic() + 30
        while not (set(capture_dir.glob("*.json")) - before) and process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                stdout, stderr = process.communicate()
                raise AssertionError(f"Codex did not start\n{stdout}\n{stderr}")
            time.sleep(0.02)
        try:
            stdout, stderr = process.communicate(input=stdin, timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise AssertionError(f"Codex did not stop\n{stdout}\n{stderr}") from None
        completed = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    else:
        completed = subprocess.run(
            argv,
            cwd=project,
            env=child_env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=90,
            umask=0o022,
        )
    created = set(capture_dir.glob("*.json")) - before
    assert len(created) == 1, (
        f"expected one Codex capture, got {created}; exit={completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    payload = json.loads(created.pop().read_text())
    runs_dir = project / ".agent" / "ai-hats" / "sessions" / "runs"
    return completed, runs_dir / f"session_{payload['session_id']}"


def test_codex_hitl_and_automate_artifacts_are_private(tmp_path: Path, ai_hats_shim: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(
        provider="codex",
        library_paths=[str(LIBRARY_DIR)],
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[LIBRARY_DIR]).init()
    init_repo(project)
    git(project, "add", ".")
    git(project, "commit", "-m", "configure ai-hats")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(_FAKE_CODEX)
    fake_codex.chmod(0o755)
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    base_home = tmp_path / "base-codex-home"
    base_home.mkdir()
    env = clean_env()
    env.update(
        {
            "AI_HATS_CACHE_HOME": str(tmp_path / "cache"),
            "AI_HATS_CODEX_BASE_HOME": str(base_home),
            "AI_HATS_LIBRARY_ROOT": str(LIBRARY_DIR),
            "AI_HATS_NO_UPDATE_CHECK": "1",
            "AI_HATS_TEST_CAPTURE_DIR": str(capture_dir),
            "AI_HATS_USER_HOME": str(tmp_path / "user-home"),
            "CODEX_HOME": str(base_home),
            "CODEX_SQLITE_HOME": str(base_home),
            "PATH": os.pathsep.join([str(fake_bin), env.get("PATH", "")]),
            "PYTHONPATH": checkout_pythonpath(REPO_ROOT),
        }
    )

    for mode, expected_exit in (("success", 0), ("error", 7), ("interrupt", 130)):
        completed, session_dir = _run(
            ai_hats_shim, project, env, capture_dir, kind="hitl", mode=mode
        )
        assert completed.returncode == expected_exit, completed.stdout + completed.stderr
        _assert_private_session(session_dir, required=CORE_ARTIFACTS)

    for mode, expected_exit in (("success", 0), ("error", 7)):
        completed, session_dir = _run(
            ai_hats_shim, project, env, capture_dir, kind="automate", mode=mode
        )
        assert completed.returncode == expected_exit, completed.stdout + completed.stderr
        required = (*CORE_ARTIFACTS, TRANSCRIPT_TXT)
        if mode == "error":
            required = (*required, REASONING_LOG)
        _assert_private_session(session_dir, required=required)
