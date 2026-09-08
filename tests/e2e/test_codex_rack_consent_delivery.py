"""e2e (HATS-1897)

flow: ordinary Codex HITL artifact assembly delivers the consent MCP server
cmds:
    ai-hats --provider codex --role assistant
expect: the launched session receives MCP config without a PoC launcher
why: an independently runnable server is not a delivered user feature
"""

from __future__ import annotations

import sys
import tomllib
import json
import os
import subprocess
import select
import shutil
import zipfile
from pathlib import Path

import pytest

from _helpers.codex_consent import session
from ai_hats.session_artifacts import BuiltArtifacts

pytestmark = pytest.mark.integration


@pytest.mark.install_heavy
def test_real_codex_initializes_required_consent_server(tmp_path, monkeypatch):
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Codex binary is not installed")
    artifacts = BuiltArtifacts()
    project, env = session(tmp_path, monkeypatch, artifacts)
    env["CODEX_HOME"] = str(tmp_path / "real-codex")
    Path(env["CODEX_HOME"]).mkdir()
    launch_dir = tmp_path / "different-launch-directory"
    launch_dir.mkdir()
    cli_args = [
        arg
        for flag, value in zip(artifacts.cli_args, artifacts.cli_args[1:])
        if flag == "-c" and value.startswith("mcp_servers.ai_hats_consent.")
        for arg in (flag, value)
    ]
    with (tmp_path / "codex.stderr").open("w+") as stderr:
        process = subprocess.Popen(
            [codex, "app-server", *cli_args],
            cwd=launch_dir,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
        )

        def request(request_id, method, params):
            process.stdin.write(
                json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                + "\n"
            )
            process.stdin.flush()
            for _ in range(50):
                assert select.select([process.stdout], [], [], 30)[0], "Codex response timeout"
                line = process.stdout.readline()
                assert line, "Codex closed stdout"
                message = json.loads(line)
                if message.get("id") == request_id:
                    return message
            pytest.fail("Codex did not return the requested response")

        try:
            initialized = request(
                1,
                "initialize",
                {
                    "clientInfo": {"name": "consent-test", "version": "1"},
                },
            )
            assert "result" in initialized, initialized
            started = request(2, "thread/start", {"cwd": str(launch_dir), "ephemeral": True})
            assert "result" in started, started
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            stderr.seek(0)
            print(stderr.read())


def test_hitl_materialization_registers_server(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_TEST_SECRET", "do-not-forward")
    artifacts = BuiltArtifacts()
    project, env = session(tmp_path, monkeypatch, artifacts)
    settings = {}
    for flag, value in zip(artifacts.cli_args, artifacts.cli_args[1:]):
        if flag == "-c" and value.startswith("mcp_servers.ai_hats_consent."):
            settings.update(tomllib.loads(value)["mcp_servers"]["ai_hats_consent"])
    assert settings["command"] == sys.executable
    assert settings["cwd"] == str(project.resolve())
    assert settings["args"] == ["-B", "-m", "ai_hats.consent_mcp.server"]
    assert settings["required"] is True
    assert "AI_HATS_SESSION_IDENTITY" in settings["env_vars"]
    assert "AI_HATS_CONSENT_WRAPPER_CONFIG" in settings["env_vars"]
    assert "AI_HATS_TEST_SECRET" not in settings["env_vars"]
    assert project.is_dir()
    assert env["AI_HATS_CONSENT_WRAPPER_CONFIG"]


@pytest.mark.parametrize("field", ["AI_HATS_SESSION_ID", "AI_HATS_CONSENT_WRAPPER_CONFIG"])
def test_foreign_session_binding_refuses_startup(tmp_path, monkeypatch, field):
    project, env = session(tmp_path, monkeypatch)
    env[field] = "foreign-session"
    result = subprocess.run(
        [sys.executable, "-m", "ai_hats.consent_mcp.server"],
        cwd=project,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "session" in result.stderr.lower()


@pytest.mark.install_heavy
def test_installed_wheel_delivers_a_startable_server(tmp_path, monkeypatch):
    from _helpers.env import clean_env
    from _helpers.repo_src import build_src

    root = Path(__file__).resolve().parents[2]
    env = clean_env()
    env.pop("AI_HATS_LIBRARY_ROOT", None)
    env["AI_HATS_USER_HOME"] = str(tmp_path / "user-home")
    env["CODEX_HOME"] = str(tmp_path / "codex-base")
    env["AI_HATS_CACHE_HOME"] = str(tmp_path / "cache")

    def run(args):
        done = subprocess.run(
            args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    source = build_src(root)
    wheels = tmp_path / "wheels"
    run(["uv", "build", "--out-dir", str(wheels), str(source)])
    wheel = next(wheels.glob("ai_hats-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "ai_hats/consent_mcp/server.py" in archive.namelist()
        metadata = archive.read(
            next(name for name in archive.namelist() if name.endswith("/METADATA"))
        ).decode()
        assert "Requires-Dist: mcp==1.28.1" in metadata
    for member in sorted((source / "packages").glob("*/pyproject.toml")):
        run(["uv", "build", "--out-dir", str(wheels), str(member.parent)])
    venv = tmp_path / "installed"
    run(["uv", "venv", "--python", sys.executable, str(venv)])
    python = venv / "bin/python"
    run(["uv", "pip", "install", "--python", str(python), *map(str, wheels.glob("*.whl"))])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "ai-hats"
    shim.write_text(f"#!{python}\nfrom ai_hats.cli import main\nmain()\n")
    shim.chmod(0o755)
    env["PATH"] = os.pathsep.join((str(bin_dir), str(venv / "bin"), os.defpath))
    project = tmp_path / "project"
    project.mkdir()
    run(["git", "init", str(project)])
    probe = """
import json, os, sys
from pathlib import Path
from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.consent_wrapper import materialize_consent_wrappers
from ai_hats.session_artifacts import BuiltArtifacts, RunMode, assemble_launch_env
from ai_hats.surfaces.codex.provider import CodexSurface
from ai_hats.session_report import consent_entry
project = Path(sys.argv[1])
ProjectConfig(provider="codex", active_role="assistant", default_role="assistant").save(project / PROJECT_CONFIG)
assembler = Assembler(project)
assembler.init()
result = assembler.composer.compose("assistant")
surface, artifacts = CodexSurface(), BuiltArtifacts()
surface.build_session_artifacts(project, result, "installed", run_mode=RunMode.HITL, artifacts=artifacts)
materialize_consent_wrappers(project, result, "installed", surface, artifacts)
session_dir = project / ".agent/ai-hats/sessions/runs/installed"
session_dir.mkdir(parents=True, exist_ok=True)
(session_dir / "role_materialization.json").write_text(json.dumps({
    "role": "assistant", "checks": [], "consent": [consent_entry(p) for p in result.consent],
}))
env = assemble_launch_env(surface, project, project / ".agent/ai-hats/sessions/runs/installed",
    session_id="installed", trace_path="", role="assistant", root_pid=str(os.getpid()),
    extra_env=artifacts.extra_env, run_mode=RunMode.HITL, claim=False)
print(json.dumps({"env": env, "args": artifacts.cli_args}))
"""
    launch = json.loads(run([str(python), "-c", probe, str(project)]))
    config = {}
    for flag, value in zip(launch["args"], launch["args"][1:]):
        if flag == "-c" and value.startswith("mcp_servers.ai_hats_consent."):
            config.update(tomllib.loads(value)["mcp_servers"]["ai_hats_consent"])
    assert config["command"] == str(python)
    session_env = {**env, **launch["env"]}
    for args in (
        ("create", "Installed consent probe", "--id", "HATS-1897"),
        ("transition", "HATS-1897", "plan"),
    ):
        prepared = subprocess.run(
            [str(python), "-m", "ai_hats_rack", *args],
            cwd=project,
            env=session_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert prepared.returncode == 0, prepared.stderr
    request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"elicitation": {"form": {}}},
                    "clientInfo": {"name": "wheel-test", "version": "1"},
                },
            }
        )
        + "\n"
    )
    with (tmp_path / "installed.stderr").open("w+") as stderr:
        process = subprocess.Popen(
            [config["command"], *config["args"]],
            cwd=config["cwd"],
            env={key: session_env[key] for key in config["env_vars"] if key in session_env},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
        )

        def receive():
            assert select.select([process.stdout], [], [], 30)[0], (
                "Installed server did not respond"
            )
            return json.loads(process.stdout.readline())

        def send(message):
            process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            process.stdin.flush()

        try:
            process.stdin.write(request)
            process.stdin.flush()
            assert receive()["result"]["serverInfo"]["name"] == "ai_hats_consent"
            send({"method": "notifications/initialized"})
            send(
                {
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "rack_transition",
                        "arguments": {"args": ["HATS-1897", "execute"]},
                    },
                }
            )
            question = receive()
            assert question["method"] == "elicitation/create", question
            send({"id": question["id"], "result": {"action": "cancel"}})
            assert receive()["result"]["structuredContent"]["execution"] == "not_started"
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            stderr.seek(0)
            print(stderr.read())
