"""e2e (HATS-1694)

flow:   a Codex session selects a composed role skill through native discovery
cmds:
    ai-hats -p codex -r native-role app-server
expect: real Codex registers session-scoped hatrack as enabled at the session-copy path and excludes another role's skill
why:    prompt diagnostics can name a skill without proving the native registry contract used by explicit $skill invocation
"""

from __future__ import annotations

import json
import os
import select
import signal
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from ptyprocess import PtyProcess

from _helpers.env import checkout_pythonpath, clean_env
from _helpers.hitl import strip_ansi
from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
_ROLE_MARKER = "HATS_1694_SESSION_ROLE_COPY"
_FOREIGN_MARKER = "HATS_1694_FOREIGN_ROLE_COPY"


def _main_checkout() -> Path:
    dot_git = REPO_ROOT / ".git"
    if dot_git.is_dir():
        return REPO_ROOT
    git_dir = Path(dot_git.read_text().partition(":")[2].strip()).resolve()
    return next(parent.parent for parent in git_dir.parents if parent.name == ".git")


def _write_role_library(root: Path) -> Path:
    library = root / "library"
    for name, marker in (("hatrack", _ROLE_MARKER), ("foreign-only", _FOREIGN_MARKER)):
        skill = library / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Test native Codex discovery.\n---\n"
            f"# {name}\n\nWhen invoked, reply exactly `{marker}` and nothing else.\n"
        )
    for role, skill in (("native-role", "hatrack"), ("foreign-role", "foreign-only")):
        role_dir = library / "roles" / role
        role_dir.mkdir(parents=True)
        (role_dir / "config.yaml").write_text(
            f"name: {role}\npriorities: [Reliability]\n"
            f"composition:\n  skills: [{skill}]\ninjection: Test role.\n"
        )
    return library


def _write_codex_probe_proxy(path: Path, real_codex: str) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('codex-cli probe')\n"
        "    raise SystemExit(0)\n"
        "if sys.argv[1:] == ['login', 'status']:\n"
        "    print('Logged in')\n"
        "    raise SystemExit(0)\n"
        f"os.execv({real_codex!r}, [{real_codex!r}, *sys.argv[1:]])\n"
    )
    path.chmod(0o755)


def test_real_codex_lists_selected_skill_from_session_home(
    tmp_path: Path, ai_hats_shim: Path
) -> None:
    """HATS-1694: the native registry must resolve the copied session skill."""
    real_codex = shutil.which("codex")
    if real_codex is None:
        pytest.skip("codex binary not found")

    library = _write_role_library(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(
        provider="codex",
        library_paths=[str(library)],
        active_role="native-role",
        default_role="native-role",
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[library]).init()

    base_home = tmp_path / "base-codex-home"
    base_home.mkdir()
    proxy_bin = tmp_path / "bin"
    proxy_bin.mkdir()
    _write_codex_probe_proxy(proxy_bin / "codex", real_codex)

    env = clean_env()
    env.update(
        {
            "AI_HATS_CACHE_HOME": str(tmp_path / "cache"),
            "AI_HATS_LIBRARY_ROOT": str(LIBRARY_DIR),
            "AI_HATS_NO_UPDATE_CHECK": "1",
            "AI_HATS_USER_HOME": str(tmp_path / "user-home"),
            "CODEX_HOME": str(base_home),
            "PATH": os.pathsep.join([str(proxy_bin), env.get("PATH", "")]),
            "PYTHONPATH": checkout_pythonpath(REPO_ROOT),
        }
    )
    env.pop("AI_HATS_CODEX_BASE_HOME", None)
    env.pop("CODEX_SQLITE_HOME", None)

    messages = [
        {
            "method": "initialize",
            "id": 0,
            "params": {
                "clientInfo": {
                    "name": "ai_hats_e2e",
                    "title": "ai-hats e2e",
                    "version": "1.0.0",
                }
            },
        },
        {"method": "initialized", "params": {}},
        {
            "method": "skills/list",
            "id": 1,
            "params": {"cwds": [str(project)], "forceReload": True},
        },
    ]
    proc = subprocess.Popen(  # noqa: S603 - test-controlled executable and argv
        [
            str(ai_hats_shim),
            "-p",
            "codex",
            "-r",
            "native-role",
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            'approval_policy="on-request"',
            "app-server",
        ],
        cwd=project,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    transcript = b""
    skills_response = None
    session_skill_body = None
    try:
        proc.stdin.write("".join(f"{json.dumps(message)}\n" for message in messages).encode())
        proc.stdin.flush()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and proc.poll() is None:
            ready, _, _ = select.select([proc.stdout.fileno()], [], [], 0.25)
            if not ready:
                continue
            chunk = os.read(proc.stdout.fileno(), 8192)
            if not chunk:
                break
            transcript += chunk
            for line in transcript.decode(errors="replace").splitlines():
                candidate = line[line.find("{") :] if "{" in line else ""
                try:
                    response = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if response.get("id") == 1 and ("result" in response or "error" in response):
                    skills_response = response
                    if "result" in response:
                        [cwd_result] = response["result"]["data"]
                        native_skill = next(
                            skill for skill in cwd_result["skills"] if skill["name"] == "hatrack"
                        )
                        session_skill_body = Path(native_skill["path"]).read_text()
                    break
            if skills_response is not None:
                break
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)

    assert skills_response is not None, strip_ansi(transcript.decode(errors="replace"))[-4000:]
    assert "error" not in skills_response, skills_response
    [result] = skills_response["result"]["data"]
    assert result["cwd"] == str(project)
    assert result["errors"] == []
    skills = {skill["name"]: skill for skill in result["skills"]}
    assert skills["hatrack"]["enabled"] is True
    session_skill_path = Path(skills["hatrack"]["path"])
    assert session_skill_path.is_relative_to(base_home / ".ai-hats" / "session-homes")
    assert session_skill_path.parts[-3:] == ("skills", "hatrack", "SKILL.md")
    assert session_skill_body is not None and _ROLE_MARKER in session_skill_body
    assert "foreign-only" not in skills
    assert not (project / ".agents").exists()
    assert not (project / ".codex").exists()


def test_workspace_seatbelt_blocks_session_symlink_write_escape(tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("Codex :workspace Seatbelt profile is macOS-specific")
    real_codex = shutil.which("codex")
    if real_codex is None:
        pytest.skip("codex binary not found")

    workspace = tmp_path / "workspace"
    session_home = workspace / "codex-home"
    session_home.mkdir(parents=True)
    local_file = workspace / "local-control"
    local_file.write_text("delete me")
    protected_dir = _main_checkout() / ".pytest_cache" / f"hats1694-seatbelt-{uuid.uuid4().hex}"
    protected_dir.mkdir(parents=True)
    protected_file = protected_dir / "shared-auth-state"
    protected_file.write_text("must survive")
    shared_link = session_home / "shared-state"
    shared_link.symlink_to(protected_dir, target_is_directory=True)

    try:
        control = subprocess.run(  # noqa: S603 - fixed rm target inside test workspace
            [
                real_codex,
                "sandbox",
                "-P",
                ":workspace",
                "-C",
                str(workspace),
                "--",
                "/bin/rm",
                "-f",
                str(local_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert control.returncode == 0, control.stdout + control.stderr
        assert not local_file.exists()

        escaped = subprocess.run(  # noqa: S603 - fixed rm target through test symlink
            [
                real_codex,
                "sandbox",
                "-P",
                ":workspace",
                "-C",
                str(workspace),
                "--",
                "/bin/rm",
                "-f",
                str(shared_link / protected_file.name),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert escaped.returncode != 0
        assert "Operation not permitted" in escaped.stderr
        assert protected_file.read_text() == "must survive"
        assert shared_link.is_symlink()
    finally:
        shutil.rmtree(protected_dir, ignore_errors=True)


@pytest.mark.skipif(
    os.environ.get("AI_HATS_CODEX_AUTH_E2E") != "1",
    reason="set AI_HATS_CODEX_AUTH_E2E=1 to run the authenticated native picker smoke",
)
def test_authenticated_pty_picker_shows_session_role_skill(
    tmp_path: Path, ai_hats_shim: Path
) -> None:
    real_codex = shutil.which("codex")
    if real_codex is None:
        pytest.skip("codex binary not found")
    configured_home = os.environ.get("AI_HATS_CODEX_BASE_HOME") or os.environ.get("CODEX_HOME")
    base_home = Path(configured_home).expanduser() if configured_home else Path.home() / ".codex"
    if not base_home.is_dir():
        pytest.skip("Codex base home is unavailable")

    library = _write_role_library(tmp_path)
    project = tmp_path / "authenticated-project"
    project.mkdir()
    ProjectConfig(
        provider="codex",
        library_paths=[str(library)],
        active_role="native-role",
        default_role="native-role",
    ).save(project / PROJECT_CONFIG)
    Assembler(project, library_paths=[library]).init()

    env = {
        key: os.environ[key]
        for key in ("HOME", "PATH", "TERM", "LANG", "LC_ALL", "LC_CTYPE")
        if key in os.environ
    }
    env.update(
        {
            "AI_HATS_CACHE_HOME": str(tmp_path / "authenticated-cache"),
            "AI_HATS_LIBRARY_ROOT": str(LIBRARY_DIR),
            "AI_HATS_NO_UPDATE_CHECK": "1",
            "AI_HATS_USER_HOME": str(tmp_path / "authenticated-user-home"),
            "CODEX_HOME": str(base_home),
            "CODEX_SQLITE_HOME": os.environ.get("CODEX_SQLITE_HOME", str(base_home)),
            "PYTHONPATH": checkout_pythonpath(REPO_ROOT),
        }
    )
    login = subprocess.run(  # noqa: S603 - fixed local Codex readiness probe
        [real_codex, "login", "status"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if login.returncode != 0:
        pytest.skip("Codex is not authenticated")

    proc = PtyProcess.spawn(
        [str(ai_hats_shim), "-p", "codex", "-r", "native-role"],
        cwd=str(project),
        env=env,
        dimensions=(40, 140),
    )
    transcript = ""
    picker_opened = False
    picker_seen = False
    exit_sent = False
    started = time.monotonic()
    deadline = started + 60
    try:
        while time.monotonic() < deadline and proc.isalive():
            ready, _, _ = select.select([proc.fd], [], [], 0.25)
            if ready:
                try:
                    transcript += proc.read(8192).decode(errors="replace")
                except EOFError:
                    break
            plain = strip_ansi(transcript).replace("\r", "")
            elapsed = time.monotonic() - started
            if not picker_opened and elapsed >= 3:
                proc.write(b"$hatrack")
                picker_opened = True
            elif picker_opened and not picker_seen and "Test native Codex discovery." in plain:
                picker_seen = True
                proc.write(b"\x1b/exit\r")
                exit_sent = True
            elif picker_opened and not exit_sent and elapsed >= 15:
                proc.write(b"\x1b/exit\r")
                exit_sent = True
        if proc.isalive():
            proc.write(b"\x03\x03\x03")
            time.sleep(0.5)
    finally:
        if proc.isalive():
            proc.terminate(force=True)
        proc.wait()

    plain = strip_ansi(transcript).replace("\r", "")
    assert picker_opened and picker_seen
    assert "hatrack" in plain
    assert "Skill" in plain
    assert _ROLE_MARKER not in plain
    assert _FOREIGN_MARKER not in plain
