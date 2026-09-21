from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import dataclasses
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from ai_hats.fs_digest import dir_digest
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import probe_host
from ai_hats.surfaces import HookEvent, apply
from ai_hats.surfaces.codex.hook_dispatcher import DISPATCHER_COMMAND, dispatch_hook
from ai_hats.surfaces.codex.provider import CodexSurface
from ai_hats.surfaces.codex.runtime_hooks import (
    CODEX_HOOK_EVENTS,
    build_hook_cli_args,
    plan_hooks,
)
from ai_hats.surfaces.plan import (
    CompositionPlan,
    Executable,
    Hooks,
    Host,
    Prompt,
    PromptBlock,
    PromptMember,
    RuntimeHook,
    Skill,
)


def _guard(tmp_path: Path, *, matcher: str = "Bash") -> tuple[Skill, RuntimeHook]:
    """A skill shipping one PreToolUse script, as the adapter hands it to a plan."""
    root = tmp_path / "source" / "guard"
    (root / "hooks").mkdir(parents=True)
    document = "---\nname: guard\n---\n# Guard\n"
    (root / "SKILL.md").write_text(document)
    script = root / "hooks" / "guard.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    skill = Skill(
        name="skills::guard",
        path=root.resolve(),
        content_digest=dir_digest(root),
        document=document,
    )
    hook = RuntimeHook(
        at=HookEvent.PRE_TOOL_USE,
        matcher=matcher,
        run=Executable(
            path=script.resolve(), content_digest=hashlib.sha256(script.read_bytes()).hexdigest()
        ),
    )
    return skill, hook


def _composition(skill: Skill, *hooks: RuntimeHook) -> CompositionPlan:
    return CompositionPlan(
        identity="guarded",
        prompt=Prompt((PromptBlock(None, (PromptMember("guarded::prompt", "# role\n", None),)),)),
        skills=(skill,),
        hooks=Hooks(runtime=tuple(hooks), external=()),
        trace=(),
    )


def _host() -> Host:
    return Host(python=Path("/opt/py/bin/python3"), path="/usr/bin", commands={})


def _pin_codex_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("AI_HATS_CODEX_BASE_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_SQLITE_HOME", str(codex_home))
    return codex_home


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

    result = subprocess.run(  # noqa: S602 — the shell IS the subject: codex runs this
        # string through `sh -c`, so a test spawning it any other way tests a
        # different thing than what ships in the TOML.
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


def test_the_manifest_names_the_mirror_and_pins_the_interpreter(tmp_path: Path) -> None:
    """Planning writes nothing: the manifest is an entry, its command points
    into the session's own mirror, the pins come from the probed host."""
    project = tmp_path / "project"
    skill, hook = _guard(tmp_path)
    root = tmp_path / "cache" / "sessions" / "sid-manifest"
    skills_root = tmp_path / "codex-home" / "skills"

    manifest, env = plan_hooks(
        _composition(skill, hook),
        root,
        _host(),
        layout=ProjectLayout.at(project),
        skills_root=skills_root,
    )

    assert manifest.target == root / "hooks.json"
    data = json.loads(manifest.content or "")
    assert data["session"] == {
        "id": "sid-manifest",
        "ai_hats_dir": str(project / ".agent" / "ai-hats"),
        "skills_root": str(skills_root),
    }
    assert data["hooks"] == {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "command": str(skills_root / "guard" / "hooks" / "guard.sh"),
                "tag": "ai-hats:guard:PreToolUse:Bash:guard",
            }
        ]
    }
    assert env == {
        "AI_HATS_SESSION_CACHE_DIR": str(root),
        "AI_HATS_PYTHON": "/opt/py/bin/python3",
    }
    assert not root.exists() and not (tmp_path / "codex-home").exists()


def test_a_hook_outside_every_composed_skill_is_refused(tmp_path: Path) -> None:
    skill, hook = _guard(tmp_path)
    stray = tmp_path / "elsewhere.sh"
    stray.write_text("#!/bin/sh\n")
    outside = dataclasses.replace(
        hook, run=Executable(path=stray.resolve(), content_digest="0" * 64)
    )

    with pytest.raises(ValueError, match="outside every composed skill"):
        plan_hooks(
            _composition(skill, outside),
            tmp_path / "root",
            _host(),
            layout=ProjectLayout.at(tmp_path / "project"),
            skills_root=tmp_path / "skills",
        )


def _surface_plan(tmp_path: Path, composition: CompositionPlan, session_id: str):
    layout = ProjectLayout.at(tmp_path / "project")
    surface = CodexSurface()
    return surface.plan(
        composition,
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=layout.cache.session(session_id),
        layout=layout,
        host=probe_host(surface=surface),
    )


def test_a_hookless_composition_wires_no_dispatcher(tmp_path: Path, monkeypatch) -> None:
    """Hookless roles are not asked to trust an inert dispatcher, and get no pins."""
    _pin_codex_home(tmp_path, monkeypatch)
    skill, _hook = _guard(tmp_path)

    plan = _surface_plan(tmp_path, _composition(skill), "sid-quiet")

    assert not any(e.target.name == "hooks.json" for e in plan.entries)
    assert not any(a.startswith("hooks.") for a in plan.launch.args)
    assert "AI_HATS_SESSION_CACHE_DIR" not in plan.env and "AI_HATS_PYTHON" not in plan.env


def test_the_plan_delivers_the_manifest_and_the_static_hook_config(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _pin_codex_home(tmp_path, monkeypatch)
    skill, hook = _guard(tmp_path)

    plan = _surface_plan(tmp_path, _composition(skill, hook), "sid-pipeline")
    apply(plan)

    manifest = ProjectLayout.at(project).cache.session("sid-pipeline") / "hooks.json"
    assert manifest in {e.target for e in plan.entries}
    data = json.loads(manifest.read_text())
    assert data["session"]["id"] == "sid-pipeline"
    assert data["hooks"]["PreToolUse"][0]["command"] == str(
        CodexSurface().session_skills_root(ProjectLayout.at(project), "sid-pipeline")
        / "guard"
        / "hooks"
        / "guard.sh"
    )
    assert Path(data["hooks"]["PreToolUse"][0]["command"]).is_file()
    assert {a.split("=", 1)[0] for a in plan.launch.args if a.startswith("hooks.")} == {
        f"hooks.{event}" for event in CODEX_HOOK_EVENTS
    }
    assert plan.env["AI_HATS_SESSION_CACHE_DIR"] == str(manifest.parent)
    assert plan.env["AI_HATS_PYTHON"] == sys.executable
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
    # The hook's own reason, plus the channel saying why the question became a
    # refusal — the half the surface's local branch used to leave out.
    assert decision["permissionDecisionReason"].startswith("set AI_HATS_PUSH_ACK=1")
    assert "cannot carry the consent" in decision["permissionDecisionReason"]


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
    # Sorted: the two payloads are judged together now, so which of
    # them appends first is a race. WHICH files the gate was handed is the
    # assertion — one row per patched file — and that is unchanged.
    assert sorted(marker.read_text().splitlines()) == [
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
    shutil.copytree(
        guard.parent / "consent_gate",
        cache / "codex-home/skills/guard/hooks/consent_gate",
    )
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


def test_a_question_on_an_arrival_codex_cannot_ask_on_is_refused_by_the_channel(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """codex can put a question only where it was already asking, and _emit
    turned an `ask` elsewhere into a bare deny of its own making.

    That is a branch of policy in a surface, and it cost the reader the wording
    the channel exists to hold in one copy — and the hatch with it.
    """
    cache = tmp_path / "cache"
    asks = _script(
        tmp_path / "ask.sh",
        "cat >/dev/null\n"
        'printf \'%s\' \'{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
        '"permissionDecision":"ask","permissionDecisionReason":"needs consent"}}\'\n',
    )
    _manifest(cache, asks)

    _code, stdout, _stderr = _run(
        {"hook_event_name": "PreToolUse", "tool_name": "exec", "tool_input": {"command": "x"}},
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    spoken = json.loads(stdout)["hookSpecificOutput"]
    assert spoken["permissionDecision"] == "deny"
    assert "cannot carry the consent" in spoken["permissionDecisionReason"], (
        f"the surface invented its own refusal instead of the channel's:\n{stdout!r}"
    )


def test_advice_is_not_silently_dropped_on_the_arrival_that_cannot_carry_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """codex's PermissionRequest reply has a `decision` object and no slot for
    advice, so _emit skipped the nudges there.

    The dialect said this surface carries them, so the channel had already
    stopped reducing them away — the drop happened after everything that could
    have accounted for it. Narrowing the capability for THIS arrival makes the
    channel do the dropping, in the one place that knows it happened.
    """
    hint = _script(tmp_path / "hint.sh", "cat >/dev/null\n" + _context_reply("prefer Grep"))
    cache = tmp_path / "cache"
    _manifest(cache, hint)

    _code, stdout, _stderr = _run(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "exec",
            "tool_input": {"command": "x"},
        },
        _session_env(cache),
        monkeypatch,
        capsys,
    )

    from ai_hats.surfaces.codex.profile import PROFILE

    assert not PROFILE.dialect("PermissionRequest").can_carry_nudges, (
        "the row still claims an arrival with nowhere to put advice can carry it"
    )
    assert PROFILE.dialect("PreToolUse").can_carry_nudges, (
        "the control: the narrowing must belong to that one arrival, not the surface"
    )
    assert "prefer Grep" not in stdout


def _context_reply(text: str) -> str:
    spoken = json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}
    )
    return f"printf '%s' {json.dumps(spoken)}\n"
