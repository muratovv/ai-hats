"""e2e (HATS-1531)

flow:   a Codex HITL session starts with the real maintainer composition
cmds:
    ai-hats -p codex -r maintainer
expect: the child inherits the requested project cwd, runtime safety denies a
        destructive Bash payload, and shutdown leaves no Codex-owned project files
why:    role delivery is insufficient if startup pollutes the repository or the
        surface bypasses the composed permission chain
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath, clean_env
from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
CODEX_SRC = REPO_ROOT / "packages/surfaces/codex/src"

_FAKE_CODEX = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import time
import sys

if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.147.0")
    raise SystemExit(0)
if sys.argv[1:] == ["login", "status"]:
    print("Logged in")
    raise SystemExit(0)

payload = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf /"},
    "cwd": os.getcwd(),
}
cache_dir = Path(os.environ["AI_HATS_SESSION_CACHE_DIR"])
barrier_dir = os.environ.get("AI_HATS_CODEX_BARRIER_DIR")
if barrier_dir:
    barrier = Path(barrier_dir)
    barrier.mkdir(parents=True, exist_ok=True)
    (barrier / (os.environ["AI_HATS_SESSION_ID"] + ".ready")).write_text(str(cache_dir))
    deadline = time.monotonic() + 30
    while len(list(barrier.glob("*.ready"))) < 2:
        if time.monotonic() >= deadline:
            raise TimeoutError("second Codex session did not reach the overlap barrier")
        time.sleep(0.02)
manifest = json.loads((cache_dir / "hooks.json").read_text())
hook = subprocess.run(
    [os.environ["AI_HATS_PYTHON"], "-m", "ai_hats_codex.hook_dispatcher"],
    input=json.dumps(payload),
    env=os.environ.copy(),
    capture_output=True,
    text=True,
    timeout=30,
)
capture = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "session_id": os.environ["AI_HATS_SESSION_ID"],
    "cache_dir": str(cache_dir),
    "manifest_session_id": manifest["session"]["id"],
    "hook_returncode": hook.returncode,
    "hook_stdout": hook.stdout,
    "hook_stderr": hook.stderr,
}
Path(os.environ["AI_HATS_CODEX_CAPTURE"]).write_text(json.dumps(capture))
"""


def _snapshot_non_agent_files(project: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        if relative.parts[0] == ".agent" or not path.is_file():
            continue
        snapshot[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def test_codex_start_is_clean_and_dispatches_composed_permissions(
    tmp_path: Path, ai_hats_shim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # HATS-1531 acceptance crosses the real launcher/provider process boundary.
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(LIBRARY_DIR))
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(
        provider="codex",
        library_paths=[str(LIBRARY_DIR)],
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[LIBRARY_DIR]).init()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(_FAKE_CODEX)
    fake_codex.chmod(0o755)
    capture_path = tmp_path / "codex-capture.json"

    before = _snapshot_non_agent_files(project)
    env = clean_env()
    env.update(
        {
            "AI_HATS_CACHE_HOME": str(tmp_path / "cache"),
            "AI_HATS_CODEX_CAPTURE": str(capture_path),
            "AI_HATS_NO_UPDATE_CHECK": "1",
            "AI_HATS_USER_HOME": str(tmp_path / "user-home"),
            "PATH": os.pathsep.join([str(fake_bin), env.get("PATH", "")]),
            "PYTHONPATH": os.pathsep.join([checkout_pythonpath(REPO_ROOT), str(CODEX_SRC)]),
        }
    )

    launched = subprocess.run(
        [str(ai_hats_shim), "-p", "codex", "-r", "maintainer"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert launched.returncode == 0, launched.stdout + launched.stderr
    capture = json.loads(capture_path.read_text())
    assert capture["cwd"] == str(project)
    assert capture["session_id"]
    assert not Path(capture["cache_dir"]).exists(), "session cache must be cleaned at exit"
    assert "--sandbox" in capture["argv"]
    assert capture["argv"][capture["argv"].index("--sandbox") + 1] == "workspace-write"
    assert any(value.startswith("hooks.PreToolUse=") for value in capture["argv"])

    assert capture["hook_returncode"] == 0, capture["hook_stderr"]
    decision = json.loads(capture["hook_stdout"])["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "No consent flag overrides" in decision["permissionDecisionReason"]

    assert _snapshot_non_agent_files(project) == before
    assert not (project / "AGENTS.md").exists()
    assert not (project / ".agents").exists()
    assert not (project / ".codex").exists()


def test_two_full_codex_sessions_overlap_without_sharing_or_leaking_state(
    tmp_path: Path, ai_hats_shim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # HATS-1531 requires concurrent top-level sessions, not only dispatcher units.
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(LIBRARY_DIR))
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(
        provider="codex",
        library_paths=[str(LIBRARY_DIR)],
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[LIBRARY_DIR]).init()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(_FAKE_CODEX)
    fake_codex.chmod(0o755)
    barrier = tmp_path / "barrier"
    captures = [tmp_path / "capture-one.json", tmp_path / "capture-two.json"]
    before = _snapshot_non_agent_files(project)

    base_env = clean_env()
    base_env.update(
        {
            "AI_HATS_CACHE_HOME": str(tmp_path / "cache"),
            "AI_HATS_CODEX_BARRIER_DIR": str(barrier),
            "AI_HATS_LIBRARY_ROOT": str(LIBRARY_DIR),
            "AI_HATS_NO_UPDATE_CHECK": "1",
            "AI_HATS_USER_HOME": str(tmp_path / "user-home"),
            "PATH": os.pathsep.join([str(fake_bin), base_env.get("PATH", "")]),
            "PYTHONPATH": os.pathsep.join([checkout_pythonpath(REPO_ROOT), str(CODEX_SRC)]),
        }
    )
    processes = [
        subprocess.Popen(
            [str(ai_hats_shim), "-p", "codex", "-r", "maintainer"],
            cwd=project,
            env={**base_env, "AI_HATS_CODEX_CAPTURE": str(capture)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for capture in captures
    ]
    try:
        results = [process.communicate(timeout=90) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert [process.returncode for process in processes] == [0, 0], results
    payloads = [json.loads(capture.read_text()) for capture in captures]
    assert payloads[0]["session_id"] != payloads[1]["session_id"]
    assert payloads[0]["cache_dir"] != payloads[1]["cache_dir"]
    for payload in payloads:
        assert payload["manifest_session_id"] == payload["session_id"]
        assert not Path(payload["cache_dir"]).exists()
        assert (
            json.loads(payload["hook_stdout"])["hookSpecificOutput"]["permissionDecision"] == "deny"
        )
    assert len(list(barrier.glob("*.ready"))) == 2
    assert _snapshot_non_agent_files(project) == before
    assert not (project / ".codex").exists()
