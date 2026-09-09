"""e2e (HATS-1801)

flow:   real Codex persists a thread under ai-hats, then the process group gets SIGINT or SIGKILL
cmds:
    ai-hats -p codex -r resume-role app-server
expect: graceful exit canonicalizes the rollout; crash retention survives cache loss; resume and native role skills work
why:    cleanup-only tests cannot prove the HATS-1801 resume invariant across process termination
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath, clean_env
from _helpers.hitl import strip_ansi
from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"


def _write_role_library(tmp_path: Path) -> Path:
    library = tmp_path / "library"
    skill = library / "skills" / "resume-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: resume-skill\ndescription: Force a native Codex session home.\n---\n"
        "# Resume skill\n"
    )
    role = library / "roles" / "resume-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: resume-role\npriorities: [Reliability]\n"
        "composition:\n  skills: [resume-skill]\ninjection: Resume test role.\n"
    )
    return library


def _write_codex_proxy(path: Path, real_codex: str) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('codex-cli resume-probe')\n"
        "    raise SystemExit(0)\n"
        "if sys.argv[1:] == ['login', 'status']:\n"
        "    print('Logged in')\n"
        "    raise SystemExit(0)\n"
        f"os.execv({real_codex!r}, [{real_codex!r}, *sys.argv[1:]])\n"
    )
    path.chmod(0o755)


def _send(process: subprocess.Popen, messages: list[dict]) -> None:
    assert process.stdin is not None
    process.stdin.write("".join(f"{json.dumps(message)}\n" for message in messages).encode())
    process.stdin.flush()


def _read_response(
    process: subprocess.Popen,
    request_id: int,
    *,
    timeout_s: float = 30,
) -> tuple[dict, str]:
    assert process.stdout is not None
    transcript = bytearray()
    pending = b""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout.fileno()], [], [], 0.25)
        if not ready:
            if process.poll() is not None:
                break
            continue
        chunk = os.read(process.stdout.fileno(), 8192)
        if not chunk:
            break
        transcript.extend(chunk)
        pending += chunk
        lines = pending.split(b"\n")
        pending = lines.pop()
        for raw_line in lines:
            line = raw_line.decode(errors="replace")
            candidate = line[line.find("{") :] if "{" in line else ""
            try:
                message = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id and ("result" in message or "error" in message):
                return message, transcript.decode(errors="replace")
    output = strip_ansi(transcript.decode(errors="replace"))
    raise AssertionError(
        f"Codex app-server returned no response for id={request_id}:\n{output[-4000:]}"
    )


def _initialize(thread_request: dict) -> list[dict]:
    return [
        {
            "method": "initialize",
            "id": 0,
            "params": {
                "capabilities": {"experimentalApi": True},
                "clientInfo": {
                    "name": "ai_hats_resume_e2e",
                    "title": "ai-hats resume e2e",
                    "version": "1.0.0",
                },
            },
        },
        {"method": "initialized", "params": {}},
        thread_request,
    ]


def _wait_for_rollout(base_home: Path, thread_id: str) -> Path:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        for database in base_home.glob("state_*.sqlite"):
            try:
                with sqlite3.connect(database, timeout=1) as connection:
                    row = connection.execute(
                        "SELECT rollout_path FROM threads WHERE id = ?",
                        (thread_id,),
                    ).fetchone()
            except sqlite3.Error:
                continue
            if row and isinstance(row[0], str):
                return Path(row[0])
        time.sleep(0.05)
    raise AssertionError(f"Codex persisted no rollout path for thread {thread_id}")


@pytest.mark.parametrize("termination", ["sigint", "sigkill"])
def test_real_codex_thread_resume_survives_session_termination(
    tmp_path: Path,
    ai_hats_shim: Path,
    termination: str,
) -> None:
    """HATS-1801: resume survives both the graceful and crash lifecycle branches."""
    real_codex = shutil.which("codex")
    if real_codex is None:
        pytest.skip("codex binary not found")

    library = _write_role_library(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(
        provider="codex",
        library_paths=[str(library)],
        active_role="resume-role",
        default_role="resume-role",
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[library]).init()

    base_home = tmp_path / "base-codex-home"
    base_home.mkdir()
    cache_home = tmp_path / "cache"
    proxy_bin = tmp_path / "bin"
    proxy_bin.mkdir()
    _write_codex_proxy(proxy_bin / "codex", real_codex)
    launch_env = clean_env()
    launch_env.update(
        {
            "AI_HATS_CACHE_HOME": str(cache_home),
            "AI_HATS_CODEX_BASE_HOME": str(base_home),
            "AI_HATS_LIBRARY_ROOT": str(LIBRARY_DIR),
            "AI_HATS_NO_UPDATE_CHECK": "1",
            "AI_HATS_USER_HOME": str(tmp_path / "user-home"),
            "CODEX_HOME": str(base_home),
            "CODEX_SQLITE_HOME": str(base_home),
            "PATH": os.pathsep.join([str(proxy_bin), launch_env.get("PATH", "")]),
            "PYTHONPATH": checkout_pythonpath(REPO_ROOT),
        }
    )

    launched = subprocess.Popen(  # noqa: S603 - fixed local test launcher
        [
            str(ai_hats_shim),
            "-p",
            "codex",
            "-r",
            "resume-role",
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            'approval_policy="on-request"',
            "app-server",
        ],
        cwd=project,
        env=launch_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        _send(
            launched,
            _initialize(
                {
                    "method": "thread/start",
                    "id": 1,
                    "params": {"cwd": str(project), "ephemeral": False},
                }
            ),
        )
        started, transcript = _read_response(launched, 1)
        assert "error" not in started, transcript[-4000:]
        thread_id = started["result"]["thread"]["id"]
        _send(
            launched,
            [
                {
                    "method": "thread/inject_items",
                    "id": 3,
                    "params": {
                        "threadId": thread_id,
                        "items": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": "resume probe"}],
                            }
                        ],
                    },
                }
            ],
        )
        injected, transcript = _read_response(launched, 3)
        assert "error" not in injected, transcript[-4000:]
        rollout = _wait_for_rollout(base_home, thread_id)
        [manifest] = list((base_home / ".ai-hats" / "session-homes").rglob(".ai-hats-session.json"))
        session_home = manifest.parent
        assert rollout.is_relative_to(session_home / "sessions")
        assert rollout.is_file()
        canonical_rollout = rollout.resolve()

        termination_signal = signal.SIGINT if termination == "sigint" else signal.SIGKILL
        os.killpg(launched.pid, termination_signal)
        launched.wait(timeout=10)
    finally:
        if launched.poll() is None:
            os.killpg(launched.pid, signal.SIGKILL)
            launched.wait(timeout=10)

    if termination == "sigkill":
        shutil.rmtree(cache_home)
        assert session_home.is_dir()
        assert rollout.is_file()
    else:
        assert not session_home.exists()
        assert _wait_for_rollout(base_home, thread_id) == canonical_rollout
        assert canonical_rollout.is_file()

    resumed = subprocess.Popen(  # noqa: S603 - fixed local test launcher
        [
            str(ai_hats_shim),
            "-p",
            "codex",
            "-r",
            "resume-role",
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            'approval_policy="on-request"',
            "app-server",
        ],
        cwd=project,
        env=launch_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        _send(
            resumed,
            _initialize(
                {
                    "method": "thread/resume",
                    "id": 2,
                    "params": {"threadId": thread_id},
                }
            ),
        )
        response, transcript = _read_response(resumed, 2)
        assert "error" not in response, transcript[-4000:]
        assert response["result"]["thread"]["id"] == thread_id
        _send(
            resumed,
            [
                {
                    "method": "skills/list",
                    "id": 4,
                    "params": {"cwds": [str(project)], "forceReload": True},
                }
            ],
        )
        skills_response, transcript = _read_response(resumed, 4)
        assert "error" not in skills_response, transcript[-4000:]
        [cwd_result] = skills_response["result"]["data"]
        role_skill = next(
            skill for skill in cwd_result["skills"] if skill["name"] == "resume-skill"
        )
        assert role_skill["enabled"] is True
        role_skill_path = Path(role_skill["path"])
        assert role_skill_path.is_relative_to(base_home / ".ai-hats" / "session-homes")
        assert role_skill_path.is_file()
    finally:
        if resumed.poll() is None:
            os.killpg(resumed.pid, signal.SIGINT)
            resumed.wait(timeout=10)
