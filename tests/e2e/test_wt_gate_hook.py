"""HATS-857 — script-level behaviour of the worktree-isolation PreToolUse gate.

Per ``dev_rule_e2e_gate`` the hook is a pure subprocess surface. We feed it Claude
Code ``PreToolUse`` payloads on stdin and assert the NON-BLOCKING contract: a
code file whose ``file_path`` is in the MAIN checkout emits
``hookSpecificOutput.additionalContext`` (the worktree nudge); a code file inside
a linked worktree, a non-code file, a non-git path, the kill switch, and a garbage
payload are all silent. It NEVER emits a ``permissionDecision`` and NEVER blocks
(exit 0 always).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = REPO_ROOT / "library/core/skills/worktree-isolation/hooks/wt_gate.py"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def repos(tmp_path):
    """A main checkout + one linked worktree, both real git work trees."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-b", "master")
    _git(main, "config", "user.email", "t@t.io")
    _git(main, "config", "user.name", "t")
    (main / "seed.txt").write_text("seed\n")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "init")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-b", "task/x", str(linked))
    return main, linked


def _run(file_path, *, env_extra=None, raw=None):
    payload = (
        raw
        if raw is not None
        else json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(file_path)},
            }
        )
    )
    env = os.environ.copy()
    env.pop("AI_HATS_WT_GATE_OFF", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )


def _ctx(res):
    out = res.stdout.strip()
    if not out:
        return None
    return json.loads(out).get("hookSpecificOutput", {}).get("additionalContext")


@pytest.mark.integration
def test_code_file_in_main_checkout_nudges(repos):
    main, _ = repos
    res = _run(main / "service.py")  # new file; parent (main) exists
    assert res.returncode == 0, res.stderr
    ctx = _ctx(res)
    assert ctx is not None, f"expected a worktree nudge, stdout={res.stdout!r}"
    assert "AI_HATS_WT_GATE_OFF" in ctx
    # Non-blocking contract: never a permissionDecision.
    assert "permissionDecision" not in res.stdout


@pytest.mark.integration
def test_code_file_in_linked_worktree_is_silent(repos):
    _, linked = repos
    res = _run(linked / "service.py")
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None, f"in-worktree edit must be silent, stdout={res.stdout!r}"


@pytest.mark.integration
def test_docs_file_in_main_is_silent(repos):
    main, _ = repos
    for name in ("README.md", "CHANGELOG.md", "notes.txt"):
        res = _run(main / name)
        assert res.returncode == 0, res.stderr
        assert _ctx(res) is None, f"{name} must be silent, stdout={res.stdout!r}"


@pytest.mark.integration
def test_config_file_in_main_nudges(repos):
    # HATS-857 review: config edits in the main checkout also collide between
    # concurrent agents, so they trigger the nudge too.
    main, _ = repos
    for name in ("settings.yaml", "app.toml", "data.json"):
        res = _run(main / name)
        assert res.returncode == 0, res.stderr
        assert _ctx(res) is not None, f"{name} should nudge, stdout={res.stdout!r}"


@pytest.mark.integration
def test_env_override_extensions(repos, tmp_path):
    main, _ = repos
    override = tmp_path / "exts.json"
    override.write_text(json.dumps({"custom": [".xyz"]}))
    env = {"AI_HATS_WT_GATE_EXTS": str(override)}
    # .xyz is now in scope ...
    res = _run(main / "thing.xyz", env_extra=env)
    assert _ctx(res) is not None, f".xyz should nudge under override, stdout={res.stdout!r}"
    # ... and .py is not (the override REPLACES the defaults).
    res = _run(main / "service.py", env_extra=env)
    assert _ctx(res) is None, f".py must be silent under override, stdout={res.stdout!r}"


@pytest.mark.integration
def test_non_git_path_is_silent(tmp_path):
    res = _run(tmp_path / "loose.py")  # tmp_path is not a git repo
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None, f"non-git path must be silent, stdout={res.stdout!r}"


@pytest.mark.integration
def test_kill_switch_is_silent(repos):
    main, _ = repos
    res = _run(main / "service.py", env_extra={"AI_HATS_WT_GATE_OFF": "1"})
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None, f"kill switch must silence the gate, stdout={res.stdout!r}"


@pytest.mark.integration
@pytest.mark.parametrize("raw", ["", "not json", "{}", '{"tool_input": {}}'])
def test_malformed_payload_fails_open(raw):
    res = _run(None, raw=raw)
    assert res.returncode == 0, res.stderr
    assert _ctx(res) is None, f"malformed payload must fail open, stdout={res.stdout!r}"
