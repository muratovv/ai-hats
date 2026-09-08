"""e2e (HATS-1755, HATS-1803)

flow: a human grants rack.transition inside Codex; review-to-done then merges
cmds:
    consent rack.transition 30
    rack transition HATS-001 done
expect: Codex merges without env acks; a stale envelope refuses the grant
why: HATS-1755 exposed a nested worktree merge and Codex recovery path
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath, clean_env
from _helpers.git import git as _git
from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration  # HATS-1755: real Codex consent boundary

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_DIR = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
TASK_ID = "HATS-001"

_PLAN_SECTIONS = (
    "\n## Requirements\nx\n## Approach & counter\nx\n"
    "## Scope & Out-of-scope\nx\n## Steps\n1. x\n## Verification Protocol\nx\n"
)

_FAKE_CODEX = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.147.0")
    raise SystemExit(0)
if sys.argv[1:] == ["login", "status"]:
    print("Logged in")
    raise SystemExit(0)

task_id = os.environ["AI_HATS_CODEX_TASK_ID"]
python = os.environ["AI_HATS_PYTHON"]
identity = json.loads(os.environ["AI_HATS_SESSION_IDENTITY"])
mode = os.environ.get("AI_HATS_CODEX_CONSENT_MODE", "happy")
issue_env = os.environ.copy()
if mode == "stale-envelope":
    stale_identity = dict(identity)
    stale_identity.pop("session_cache_dir", None)
    issue_env["AI_HATS_SESSION_IDENTITY"] = json.dumps(stale_identity)
consent_path = shutil.which("consent") or ""
consent_is_file = Path(consent_path).is_file() if consent_path else False
consent_is_executable = os.access(consent_path, os.X_OK) if consent_path else False
if consent_path:
    issued = subprocess.run(
        ["consent", "rack.transition", "30"],
        cwd=os.getcwd(),
        env=issue_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
else:
    issued = subprocess.CompletedProcess(
        args=["consent", "rack.transition", "30"],
        returncode=127,
        stdout="",
        stderr="consent: command not found",
    )
if mode == "stale-envelope":
    store = Path(identity["session_cache_dir"]) / "consent" / "grants"
    capture = {
        "identity": stale_identity,
        "cache_scalar": issue_env.get("AI_HATS_SESSION_CACHE_DIR", ""),
        "ack_keys": sorted(
            key
            for key in ("AI_HATS_CONSENT_ACK", "AI_HATS_MERGE_ACK")
            if key in issue_env
        ),
        "consent_path": consent_path,
        "consent_is_file": consent_is_file,
        "consent_is_executable": consent_is_executable,
        "issued": {
            "returncode": issued.returncode,
            "stdout": issued.stdout,
            "stderr": issued.stderr,
        },
        "grant_count": len(list(store.glob("*.json"))) if store.is_dir() else 0,
    }
    Path(os.environ["AI_HATS_CODEX_CAPTURE"]).write_text(json.dumps(capture))
    raise SystemExit(0)
command = f"rack transition {task_id} done"
payload = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": command},
    "cwd": os.getcwd(),
}
hook = subprocess.run(
    [python, "-m", "ai_hats.surfaces.codex.hook_dispatcher"],
    input=json.dumps(payload),
    cwd=os.getcwd(),
    env=os.environ.copy(),
    capture_output=True,
    text=True,
    timeout=30,
)
moved = subprocess.run(
    ["rack", "transition", task_id, "done"],
    cwd=os.getcwd(),
    env=os.environ.copy(),
    capture_output=True,
    text=True,
    timeout=120,
)
seen = subprocess.run(
    ["rack", "context", task_id, "--json"],
    cwd=os.getcwd(),
    env=os.environ.copy(),
    capture_output=True,
    text=True,
    timeout=30,
)
store = Path(identity["session_cache_dir"]) / "consent" / "grants"
grant_files = sorted(store.glob("*.json"))
grant = json.loads(grant_files[0].read_text()) if len(grant_files) == 1 else {}
# Both homes (HATS-1634): a bypass that knows its session lands beside that
# session's audit.md, and only one that cannot stays under .git.
journals = [Path(os.getcwd()) / ".git" / "ai-hats" / "bypasses.jsonl"]
journals += sorted(
    (Path(os.getcwd()) / ".agent/ai-hats/sessions/runs").glob("session_*/bypasses.jsonl")
)
capture = {
    "identity": identity,
    "ack_keys": sorted(
        key
        for key in ("AI_HATS_CONSENT_ACK", "AI_HATS_MERGE_ACK")
        if key in os.environ
    ),
    "consent_path": consent_path,
    "consent_is_file": consent_is_file,
    "consent_is_executable": consent_is_executable,
    "issued": {
        "returncode": issued.returncode,
        "stdout": issued.stdout,
        "stderr": issued.stderr,
    },
    "hook": {
        "returncode": hook.returncode,
        "stdout": hook.stdout,
        "stderr": hook.stderr,
    },
    "moved": {
        "returncode": moved.returncode,
        "stdout": moved.stdout,
        "stderr": moved.stderr,
    },
    "context": json.loads(seen.stdout) if seen.returncode == 0 else {},
    "context_stderr": seen.stderr,
    "grant_count": len(grant_files),
    "grant_id": grant.get("id", ""),
    "journal": "".join(j.read_text() for j in journals if j.is_file()),
    "merged_content": (Path(os.getcwd()) / "consent-merged.txt").read_text()
    if (Path(os.getcwd()) / "consent-merged.txt").is_file()
    else "",
}
Path(os.environ["AI_HATS_CODEX_CAPTURE"]).write_text(json.dumps(capture))
raise SystemExit(0)
"""


def _rack(project: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - worktree interpreter plus literal module
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _project(tmp_path: Path, library_dir: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "Test")
    (project / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    _git(project, "add", ".gitignore")
    _git(project, "commit", "-m", "init")
    ProjectConfig(
        provider="codex",
        library_paths=[str(library_dir)],
        active_role="assistant",
        default_role="assistant",
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[library_dir]).init()
    return project


def _command_bin(tmp_path: Path) -> Path:
    """The session PATH entry: every checkout surface, plus the Codex stand-in.

    The surfaces come from the writer the whole tier shares. A second table here
    would drift from the consent registry unnoticed — the hole HATS-1847 closed.
    """
    from _helpers.surfaces import write_surface_shims

    fake_bin = write_surface_shims(tmp_path / "bin")
    codex = fake_bin / "codex"
    codex.write_text(_FAKE_CODEX)
    codex.chmod(0o755)
    return fake_bin


def _reviewable(project: Path, env: dict[str, str]) -> None:
    created = _rack(
        project,
        env,
        "create",
        "Codex consent probe",
        "--id",
        TASK_ID,
        "--role",
        "assistant",
    )
    assert created.returncode == 0, created.stderr
    planned = _rack(project, env, "transition", TASK_ID, "plan")
    assert planned.returncode == 0, planned.stderr
    plan = project / TASKS_SUB / TASK_ID / "plan.md"
    plan.write_text(plan.read_text(encoding="utf-8") + _PLAN_SECTIONS, encoding="utf-8")
    started = _rack(
        project,
        {**env, "AI_HATS_CONSENT_ACK": "1"},
        "transition",
        TASK_ID,
        "execute",
    )
    assert started.returncode == 0, started.stderr
    worktree_line = next(
        line for line in started.stdout.splitlines() if line.strip().startswith("Worktree:")
    )
    worktree = Path(worktree_line.split(":", 1)[1].strip())
    (worktree / "consent-merged.txt").write_text("merged under interactive consent\n")
    _git(worktree, "add", "consent-merged.txt")
    _git(worktree, "-c", "core.hooksPath=/dev/null", "commit", "-m", "test: add merge proof")
    documented = _rack(project, env, "transition", TASK_ID, "document")
    assert documented.returncode == 0, documented.stderr
    review = _rack(project, env, "transition", TASK_ID, "review")
    assert review.returncode == 0, review.stderr


def _launch_env(
    tmp_path: Path,
    fake_bin: Path,
    capture: Path,
    library_dir: Path,
    *,
    mode: str = "happy",
) -> dict[str, str]:
    env = clean_env()
    for stale in (
        "AI_HATS_CONSENT_ACK",
        "AI_HATS_CONSENT_TICKET",
        "AI_HATS_CODEX_BASE_HOME",
        "AI_HATS_CONSENT_BIN",
        "AI_HATS_PYTHON",
        "AI_HATS_MERGE_ACK",
        "AI_HATS_PLAN_ACK",
        "AI_HATS_ROLE",
        "AI_HATS_SESSION_CACHE_DIR",
        "AI_HATS_SESSION_IDENTITY",
        "AI_HATS_VENV",
        "CODEX_SQLITE_HOME",
    ):
        env.pop(stale, None)
    base_home = tmp_path / "base-codex-home"
    base_home.mkdir()
    env.update(
        {
            "AI_HATS_CACHE_HOME": str(tmp_path / "cache"),
            "AI_HATS_CODEX_CAPTURE": str(capture),
            "AI_HATS_CODEX_CONSENT_MODE": mode,
            "AI_HATS_CODEX_TASK_ID": TASK_ID,
            "AI_HATS_LIBRARY_ROOT": str(library_dir),
            "AI_HATS_NO_UPDATE_CHECK": "1",
            "AI_HATS_USER_HOME": str(tmp_path / "user-home"),
            "CODEX_HOME": str(base_home),
            "PATH": os.pathsep.join([str(fake_bin), os.defpath]),
            "PYTHONPATH": checkout_pythonpath(REPO_ROOT),
        }
    )
    return env


def test_codex_session_issues_and_consumes_rack_grant(tmp_path: Path, ai_hats_shim: Path) -> None:
    """HATS-1803: the bare grant verb belongs to the launched session."""
    library_dir = tmp_path / "library"
    shutil.copytree(LIBRARY_DIR, library_dir)
    project = _project(tmp_path, library_dir)
    fake_bin = _command_bin(tmp_path)
    capture_path = tmp_path / "capture.json"
    env = _launch_env(tmp_path, fake_bin, capture_path, library_dir)
    _reviewable(project, env)

    launched = subprocess.run(  # noqa: S603 - pytest fixture writes this executable
        [str(ai_hats_shim), "-p", "codex", "-r", "assistant"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert launched.returncode == 0, launched.stdout + launched.stderr
    capture = json.loads(capture_path.read_text())
    identity = capture["identity"]
    assert identity["id"]
    assert identity["project_dir"] == str(project)
    assert identity["session_dir"]
    assert identity["session_cache_dir"]
    assert capture["consent_path"], capture["issued"]
    consent_path = Path(capture["consent_path"])
    assert consent_path == Path(identity["session_cache_dir"]) / "consent-wrapper/bin/consent"
    assert capture["consent_is_file"]
    assert capture["consent_is_executable"]
    assert not consent_path.exists()
    assert not (fake_bin / "consent").exists()
    assert not (tmp_path / "user-home" / "consent").exists()
    assert capture["ack_keys"] == []
    assert capture["issued"]["returncode"] == 0, capture["issued"]
    assert "rack.transition" in capture["issued"]["stdout"]
    assert capture["grant_count"] == 1
    assert capture["grant_id"]
    assert capture["grant_id"] not in (capture["issued"]["stdout"] + capture["issued"]["stderr"])
    assert capture["hook"]["returncode"] == 0, capture["hook"]
    assert "permissionDecision" not in capture["hook"]["stdout"]
    assert capture["moved"]["returncode"] == 0, capture["moved"]
    assert capture["context"]["task"]["state"] == "done"
    assert capture["merged_content"] == "merged under interactive consent\n"
    work_log = "\n".join(entry["message"] for entry in capture["context"]["task"]["work_log"])
    assert "supervisor consent grant accepted" not in work_log
    assert "consent grant" in capture["journal"]


def test_codex_stale_envelope_refuses_grant_with_restart_recovery(
    tmp_path: Path, ai_hats_shim: Path
) -> None:
    library_dir = tmp_path / "library"
    shutil.copytree(LIBRARY_DIR, library_dir)
    project = _project(tmp_path, library_dir)
    fake_bin = _command_bin(tmp_path)
    capture_path = tmp_path / "capture.json"
    env = _launch_env(
        tmp_path,
        fake_bin,
        capture_path,
        library_dir,
        mode="stale-envelope",
    )

    launched = subprocess.run(  # noqa: S603 - pytest fixture writes this executable
        [str(ai_hats_shim), "-p", "codex", "-r", "assistant"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert launched.returncode == 0, launched.stdout + launched.stderr
    capture = json.loads(capture_path.read_text())
    assert "session_cache_dir" not in capture["identity"]
    assert capture["cache_scalar"], "the mutable scalar must not rescue a stale envelope"
    assert capture["ack_keys"] == []
    assert capture["issued"]["returncode"] == 2
    assert "publishes no cache dir" in capture["issued"]["stderr"]
    assert "Restart the session" in capture["issued"]["stderr"]
    assert capture["grant_count"] == 0
