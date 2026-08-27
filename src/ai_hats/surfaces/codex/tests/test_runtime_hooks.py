from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

from ai_hats.paths import session_cache_dir
from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats.surfaces.codex.hook_dispatcher import DISPATCHER_COMMAND, dispatch_hook
from ai_hats.surfaces.codex.provider import CodexSurface
from ai_hats.surfaces.codex.runtime_hooks import (
    CODEX_HOOK_EVENTS,
    build_hook_cli_args,
    materialize_hook_manifest,
)


def _skill(tmp_path: Path, *, matcher: str = "Bash") -> SimpleNamespace:
    root = tmp_path / "source" / "guard"
    (root / "hooks").mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        "name: guard\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        f"      - matcher: {matcher}\n"
        "        script: hooks/guard.sh\n"
        "---\n"
        "# Guard\n"
    )
    (root / "hooks" / "guard.sh").write_text("#!/bin/sh\nexit 0\n")
    return SimpleNamespace(name="guard", source_path=root)


def _manifest(
    cache: Path,
    script: Path,
    *,
    session_id: str = "sid-one",
    ai_hats_dir: str = "/project/.agent/ai-hats",
    matcher: str = "Bash",
) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    mirrored = cache / "codex-home" / "skills" / "guard" / "hooks" / script.name
    mirrored.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script, mirrored)
    (cache / "hooks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session": {"id": session_id, "ai_hats_dir": ai_hats_dir},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": matcher,
                            "command": str(mirrored),
                            "tag": "ai-hats:guard:PreToolUse:test",
                        }
                    ]
                },
            }
        )
    )


def _session_env(cache: Path, *, session_id: str = "sid-one") -> dict[str, str]:
    return {
        **os.environ,
        "AI_HATS_SESSION_ID": session_id,
        "AI_HATS_DIR": "/project/.agent/ai-hats",
        "AI_HATS_SESSION_CACHE_DIR": str(cache),
    }


def _run(
    payload: dict,
    env: dict[str, str],
    monkeypatch,
    capsys,
) -> tuple[int, str, str]:
    for key in (
        "AI_HATS_SESSION_ID",
        "AI_HATS_DIR",
        "AI_HATS_SESSION_CACHE_DIR",
    ):
        monkeypatch.setenv(key, env[key])
    code = dispatch_hook(stdin=io.StringIO(json.dumps(payload)))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    path.chmod(0o755)
    return path


def test_inline_hook_definition_is_static_toml_and_never_bypasses_trust() -> None:
    args = build_hook_cli_args()

    assert args[::2] == ["-c"] * len(CODEX_HOOK_EVENTS)
    overrides = args[1::2]
    assert {row.split("=", 1)[0] for row in overrides} == {
        f"hooks.{event}" for event in CODEX_HOOK_EVENTS
    }
    for override in overrides:
        event = override.split("=", 1)[0].split(".", 1)[1]
        parsed = tomllib.loads(override)
        command = parsed["hooks"][event][0]["hooks"][0]["command"]
        assert command == DISPATCHER_COMMAND
        assert "AI_HATS_SESSION_ID" in override
        assert "AI_HATS_SESSION_CACHE_DIR" not in override
        assert "dangerously-bypass-hook-trust" not in override
        assert "sid-" not in override


def test_static_dispatcher_fails_closed_when_runtime_identity_is_missing() -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"AI_HATS_SESSION_ID", "AI_HATS_DIR", "AI_HATS_PYTHON"}
    }

    result = subprocess.run(
        DISPATCHER_COMMAND,
        shell=True,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "incomplete dispatcher environment" in result.stderr


def test_materializes_composed_manifest_only_in_the_session_cache(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    skill = _skill(tmp_path)
    result = SimpleNamespace(skills=[skill])
    skills_dir = tmp_path / "mirror" / "skills"
    mirrored = skills_dir / "guard" / "hooks" / "guard.sh"
    mirrored.parent.mkdir(parents=True)
    mirrored.write_text("#!/bin/sh\nexit 0\n")
    artifacts = BuiltArtifacts()

    path = materialize_hook_manifest(
        project,
        result,
        "sid-manifest",
        artifacts,
        skills_dir=skills_dir,
    )

    data = json.loads(path.read_text())
    assert data["session"] == {
        "id": "sid-manifest",
        "ai_hats_dir": str(project / ".agent" / "ai-hats"),
        "skills_root": str(skills_dir),
    }
    assert data["hooks"]["PreToolUse"][0]["command"] == str(mirrored)
    assert artifacts.extra_env["AI_HATS_SESSION_CACHE_DIR"] == str(path.parent)
    assert artifacts.extra_env["AI_HATS_PYTHON"]
    assert path in artifacts.materialized
    assert not (project / ".codex").exists()
    assert not (tmp_path / "home" / ".codex").exists()


def test_provider_artifact_pipeline_delivers_manifest_and_static_hook_config(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("AI_HATS_CODEX_BASE_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(codex_home))
    skill = _skill(tmp_path)
    result = SimpleNamespace(
        skills=[skill],
        priorities=[],
        merged_injection="",
        rules=[],
        user_rules=(),
    )

    artifacts = CodexSurface().build_session_artifacts(
        project,
        result,
        "sid-pipeline",
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )

    manifest = session_cache_dir(project, "sid-pipeline") / "hooks.json"
    assert manifest in artifacts.materialized
    assert json.loads(manifest.read_text())["session"]["id"] == "sid-pipeline"
    overrides = artifacts.cli_args[1::2]
    assert {value.split("=", 1)[0] for value in overrides if value.startswith("hooks.")} == {
        f"hooks.{event}" for event in CODEX_HOOK_EVENTS
    }
    assert not (project / ".codex").exists()


def test_pretooluse_translates_claude_deny_to_codex_deny(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(
        tmp_path / "deny.sh",
        'printf \'%s\\n\' \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecision":"deny","permissionDecisionReason":"blocked"}}\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)

    code, stdout, stderr = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "cwd": str(tmp_path),
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    assert code == 0, stderr
    decision = json.loads(stdout)["hookSpecificOutput"]
    assert decision == {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "blocked",
    }


def test_permission_request_translates_deny_to_codex_permission_shape(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(
        tmp_path / "deny.sh",
        'printf \'%s\\n\' \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecision":"deny","permissionDecisionReason":"policy"}}\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)

    code, stdout, stderr = _run(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "cwd": str(tmp_path),
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    assert code == 0, stderr
    assert json.loads(stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "deny", "message": "policy"},
        }
    }


def test_permission_request_ask_defers_to_codex_native_prompt(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(
        tmp_path / "ask.sh",
        'printf \'%s\\n\' \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecision":"ask","permissionDecisionReason":"ask user"}}\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)

    code, stdout, stderr = _run(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "cwd": str(tmp_path),
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    assert code == 0, stderr
    assert stdout == ""


def test_pretooluse_ask_fails_closed_with_the_hook_recovery_reason(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(
        tmp_path / "ask.sh",
        'printf \'%s\\n\' \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecision":"ask","permissionDecisionReason":"set AI_HATS_PUSH_ACK=1"}}\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)

    code, stdout, stderr = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "cwd": str(tmp_path),
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    assert code == 0, stderr
    decision = json.loads(stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert decision["permissionDecisionReason"] == "set AI_HATS_PUSH_ACK=1"


def test_apply_patch_is_adapted_to_claude_style_file_path(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    marker = tmp_path / "seen.txt"
    hook = _script(
        tmp_path / "capture.py",
        f"python3 -c 'import json,sys; p=json.load(sys.stdin); "
        f'open({json.dumps(str(marker))}, "a").write('
        'p["tool_input"]["file_path"] + "\\n")\'\n',
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook, matcher="Edit|Write|MultiEdit")

    code, stdout, stderr = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: src/one.py\n@@\n-x\n+y\n"
                "*** Add File: src/two.py\n+z\n*** End Patch"
            },
            "cwd": str(tmp_path),
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    assert code == 0, stderr
    assert stdout == ""
    assert marker.read_text().splitlines() == [
        str((tmp_path / "src" / "one.py").resolve()),
        str((tmp_path / "src" / "two.py").resolve()),
    ]


def test_two_sessions_read_only_their_own_manifest(tmp_path: Path, monkeypatch, capsys) -> None:
    marker_one = tmp_path / "one.txt"
    marker_two = tmp_path / "two.txt"
    hook_one = _script(tmp_path / "one.sh", f"echo one >> {marker_one!s}\n")
    hook_two = _script(tmp_path / "two.sh", f"echo two >> {marker_two!s}\n")
    cache_one = tmp_path / "cache-one"
    cache_two = tmp_path / "cache-two"
    _manifest(cache_one, hook_one, session_id="sid-one")
    _manifest(cache_two, hook_two, session_id="sid-two")
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo safe"},
        "cwd": str(tmp_path),
    }

    assert _run(payload, _session_env(cache_one, session_id="sid-one"), monkeypatch, capsys)[0] == 0
    assert marker_one.read_text().strip() == "one"
    assert not marker_two.exists()

    assert _run(payload, _session_env(cache_two, session_id="sid-two"), monkeypatch, capsys)[0] == 0
    assert marker_one.read_text().strip() == "one"
    assert marker_two.read_text().strip() == "two"


def test_two_overlapping_dispatcher_processes_keep_session_state_disjoint(
    tmp_path: Path,
) -> None:
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    output_one = tmp_path / "session-one.txt"
    output_two = tmp_path / "session-two.txt"

    def overlapping_hook(label: str) -> Path:
        return _script(
            tmp_path / f"{label}.sh",
            f'touch "$AI_HATS_TEST_BARRIER/{label}.ready"\n'
            'while [ ! -f "$AI_HATS_TEST_BARRIER/one.ready" ] '
            '|| [ ! -f "$AI_HATS_TEST_BARRIER/two.ready" ]; do sleep 0.01; done\n'
            "python3 -c 'import json, os, sys; p=json.load(sys.stdin); "
            'open(os.environ["AI_HATS_TEST_OUTPUT"], "w").write('
            f'"{label}|" + os.environ["AI_HATS_SESSION_ID"] + "|" + '
            'p["tool_input"]["command"])\'\n',
        )

    cache_one = tmp_path / "cache-one"
    cache_two = tmp_path / "cache-two"
    _manifest(cache_one, overlapping_hook("one"), session_id="sid-one")
    _manifest(cache_two, overlapping_hook("two"), session_id="sid-two")

    def process_env(cache: Path, session_id: str, output: Path) -> dict[str, str]:
        env = _session_env(cache, session_id=session_id)
        codex_src = Path(__file__).resolve().parents[4]
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(codex_src), env.get("PYTHONPATH")) if value
        )
        env["AI_HATS_TEST_BARRIER"] = str(barrier)
        env["AI_HATS_TEST_OUTPUT"] = str(output)
        return env

    def payload(command: str) -> str:
        return json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "cwd": str(tmp_path),
            }
        )

    command = [sys.executable, "-m", "ai_hats.surfaces.codex.hook_dispatcher"]
    processes = [
        subprocess.Popen(
            command,
            cwd=tmp_path,
            env=process_env(cache_one, "sid-one", output_one),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ),
        subprocess.Popen(
            command,
            cwd=tmp_path,
            env=process_env(cache_two, "sid-two", output_two),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ),
    ]
    try:
        # Feed both processes before waiting on either.  Each dispatcher can
        # now enter its hook and rendezvous at the filesystem barrier while
        # the sibling is genuinely live, rather than being serialized by the
        # test's first communicate() call.
        for process, message in zip(
            processes, (payload("command-one"), payload("command-two")), strict=True
        ):
            assert process.stdin is not None
            process.stdin.write(message)
            process.stdin.close()
            process.stdin = None
        results = [
            processes[0].communicate(timeout=10),
            processes[1].communicate(timeout=10),
        ]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    assert [process.returncode for process in processes] == [0, 0]
    assert results == [("", ""), ("", "")]
    assert {path.name for path in barrier.iterdir()} == {"one.ready", "two.ready"}
    assert output_one.read_text() == "one|sid-one|command-one"
    assert output_two.read_text() == "two|sid-two|command-two"


def test_torn_session_identity_fails_closed_without_running_foreign_hook(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    marker = tmp_path / "ran.txt"
    hook = _script(tmp_path / "foreign.sh", f"echo bad > {marker!s}\n")
    cache = tmp_path / "cache"
    _manifest(cache, hook, session_id="sid-other")

    code, stdout, stderr = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo safe"},
            "cwd": str(tmp_path),
        },
        _session_env(cache, session_id="sid-current"),
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert "session identity mismatch" in stderr
    assert not marker.exists()
    # The status alone named no way past it, which is the half a delivery
    # refusal on this channel owes the human.
    spoken = json.loads(stdout)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny"
    assert "AI_HATS_GATE_BROKEN_ACK" in spoken["permissionDecisionReason"]


def test_manifest_command_outside_session_mirror_fails_closed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    marker = tmp_path / "ran.txt"
    hook = _script(tmp_path / "outside.sh", f"echo bad > {marker!s}\n")
    cache = tmp_path / "cache"
    _manifest(cache, hook)
    data = json.loads((cache / "hooks.json").read_text())
    data["hooks"]["PreToolUse"][0]["command"] = str(hook)
    (cache / "hooks.json").write_text(json.dumps(data))

    code, stdout, stderr = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo safe"},
            "cwd": str(tmp_path),
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    assert code == 2
    assert "escapes the session skills mirror" in stderr
    assert not marker.exists()
    spoken = json.loads(stdout)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny"
    assert "AI_HATS_GATE_BROKEN_ACK" in spoken["permissionDecisionReason"]


def test_exit_two_from_claude_hook_denies_permission_request(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hook = _script(tmp_path / "exit-two.sh", "echo 'hard blocked' >&2\nexit 2\n")
    cache = tmp_path / "cache"
    _manifest(cache, hook)

    code, stdout, stderr = _run(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "git push"},
            "cwd": str(tmp_path),
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    assert code == 0, stderr
    assert json.loads(stdout)["hookSpecificOutput"]["decision"] == {
        "behavior": "deny",
        "message": "hard blocked",
    }


def test_real_safety_guard_deny_survives_the_codex_adapter(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = Path(__file__).resolve().parents[5]
    guard = (
        repo
        / "packages/ai-hats-library/src/ai_hats_library/core/skills"
        / "safety-guard/hooks/safety_gate.py"
    )
    cache = tmp_path / "cache"
    _manifest(cache, guard)
    monkeypatch.delenv("AI_HATS_YOLO", raising=False)

    code, stdout, stderr = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "cwd": str(tmp_path),
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    assert code == 0, stderr
    decision = json.loads(stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "No consent flag overrides" in decision["permissionDecisionReason"]


def test_real_worktree_gate_blocks_main_but_allows_the_linked_worktree(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = Path(__file__).resolve().parents[5]
    guard = (
        repo
        / "packages/ai-hats-library/src/ai_hats_library/core/skills"
        / "worktree-isolation/hooks/wt_gate.py"
    )
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=main, check=True)
    (main / "src").mkdir()
    (main / "src" / "app.py").write_text("old = True\n")
    subprocess.run(["git", "add", "src/app.py"], cwd=main, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ai-hats-test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=main,
        check=True,
    )
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "task/hook-probe", str(linked)],
        cwd=main,
        check=True,
    )
    cache = tmp_path / "cache"
    _manifest(cache, guard, matcher="Edit|Write|MultiEdit")
    monkeypatch.delenv("AI_HATS_WT_GATE_OFF", raising=False)

    def payload(cwd: Path) -> dict:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n"
                "*** End Patch"
            },
            "cwd": str(cwd),
        }

    main_result = _run(payload(main), _session_env(cache), monkeypatch, capsys)
    linked_result = _run(payload(linked), _session_env(cache), monkeypatch, capsys)

    assert main_result[0] == 0
    assert json.loads(main_result[1])["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert linked_result == (0, "", "")


def test_an_allowing_hooks_stderr_still_reaches_the_operator(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The audit trail rides stderr, and an ALLOW is where it rides alone.

    A previous cut of this dispatcher forwarded every hook's stderr with a
    comment saying why; losing that line took the bypass journal's own
    "NOT RECORDED" warning off this surface entirely, with nothing left to say
    a hatch had been used.
    """
    hook = _script(
        tmp_path / "noisy.sh", "cat >/dev/null; echo '[bypass-journal] NOT RECORDED' >&2"
    )
    cache = tmp_path / "cache"
    _manifest(cache, hook)

    code, _stdout, stderr = _run(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "exec",
            "tool_input": {"command": "echo safe"},
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    assert code == 0
    assert "NOT RECORDED" in stderr, f"the hook's only trace was dropped:\n{stderr!r}"


def test_advice_gathered_before_a_refusal_still_reaches_the_model(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """`can_carry_nudges=True` says this surface holds them, so the channel
    stops reducing them away — and then _emit dropped them on every non-allow,
    losing what the gates BEFORE the objector had to say."""
    cache = tmp_path / "cache"
    mirror = cache / "codex-home" / "skills" / "guard" / "hooks"
    mirror.mkdir(parents=True, exist_ok=True)
    hint = _script(
        mirror / "hint.sh",
        "cat >/dev/null\n"
        'printf \'%s\' \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"additionalContext":"prefer Grep"}}\'\n',
    )
    guard = _script(
        mirror / "guard.sh",
        "cat >/dev/null\n"
        'printf \'%s\' \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecision":"deny","permissionDecisionReason":"no"}}\'\n',
    )
    (cache / "hooks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session": {"id": "sid-one", "ai_hats_dir": "/project/.agent/ai-hats"},
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "command": str(hint), "tag": "ai-hats:hint"},
                        {"matcher": "Bash", "command": str(guard), "tag": "ai-hats:guard"},
                    ]
                },
            }
        )
    )

    _code, stdout, _stderr = _run(
        {"hook_event_name": "PreToolUse", "tool_name": "exec", "tool_input": {"command": "x"}},
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    spoken = json.loads(stdout)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny"
    assert "prefer Grep" in spoken.get("additionalContext", ""), (
        f"the advice was dropped with the refusal:\n{stdout!r}"
    )
