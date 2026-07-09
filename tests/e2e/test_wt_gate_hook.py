"""HATS-857/HATS-889 — script-level behaviour of the worktree-isolation PreToolUse gate.

Per ``dev_rule_e2e_gate`` the hook is a pure subprocess surface: we feed it Claude Code
``PreToolUse`` payloads on stdin. BLOCKING contract — a code/config file in the MAIN
checkout emits ``permissionDecision == "deny"`` (HATS-889 turned the old nudge into a
hard deny); a linked-worktree file, a non-trigger file, a gitignored path, a non-git
path, the kill switch, and a garbage payload are all silent. Exit is always 0 — the deny
rides in JSON (a final decision that binds headless too), not a non-zero exit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.constants import HOOK_PRE_TOOL_USE

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


def _run(file_path, *, env_extra=None, raw=None, cwd=None):
    body = {
        "hook_event_name": HOOK_PRE_TOOL_USE,
        "tool_name": "Edit",
        "tool_input": {"file_path": str(file_path)},
    }
    if cwd is not None:
        body["cwd"] = str(cwd)  # HATS-959: session repo the gate scopes to
    payload = raw if raw is not None else json.dumps(body)
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


def _decision(res):
    """(permissionDecision, permissionDecisionReason) from the hook's JSON, or
    (None, None) when the hook stayed silent (no stdout)."""
    out = res.stdout.strip()
    if not out:
        return None, None
    hso = json.loads(out).get("hookSpecificOutput", {})
    return hso.get("permissionDecision"), hso.get("permissionDecisionReason")


@pytest.mark.integration
def test_code_file_in_main_checkout_is_denied(repos):
    main, _ = repos
    res = _run(main / "service.py")  # new file; parent (main) exists
    assert res.returncode == 0, res.stderr  # deny is carried in JSON, not a non-zero exit
    decision, reason = _decision(res)
    assert decision == "deny", f"expected a hard deny, stdout={res.stdout!r}"
    # Reason points at the worktree-isolation skill, which carries the recovery recipe.
    assert reason and "worktree-isolation" in reason


@pytest.mark.integration
def test_code_file_in_linked_worktree_is_silent(repos):
    _, linked = repos
    res = _run(linked / "service.py")
    assert res.returncode == 0, res.stderr
    assert _decision(res) == (None, None), f"in-worktree edit must be silent, {res.stdout!r}"


@pytest.mark.integration
def test_docs_file_in_main_is_silent(repos):
    main, _ = repos
    for name in ("README.md", "CHANGELOG.md", "notes.txt"):
        res = _run(main / name)
        assert res.returncode == 0, res.stderr
        assert _decision(res) == (None, None), f"{name} must be silent, {res.stdout!r}"


@pytest.mark.integration
def test_config_file_in_main_is_denied(repos):
    # HATS-857 review: config edits in the main checkout also collide between
    # concurrent agents, so they are denied too.
    main, _ = repos
    for name in ("settings.yaml", "app.toml", "data.json"):
        res = _run(main / name)
        assert res.returncode == 0, res.stderr
        assert _decision(res)[0] == "deny", f"{name} should deny, {res.stdout!r}"


@pytest.mark.integration
def test_env_override_extensions(repos, tmp_path):
    main, _ = repos
    override = tmp_path / "exts.json"
    override.write_text(json.dumps({"custom": [".xyz"]}))
    env = {"AI_HATS_WT_GATE_EXTS": str(override)}
    # .xyz is now in scope ...
    res = _run(main / "thing.xyz", env_extra=env)
    assert _decision(res)[0] == "deny", f".xyz should deny under override, {res.stdout!r}"
    # ... and .py is not (the override REPLACES the defaults).
    res = _run(main / "service.py", env_extra=env)
    assert _decision(res) == (None, None), f".py must be silent under override, {res.stdout!r}"


@pytest.mark.integration
def test_gitignored_file_in_main_is_silent(repos):
    # Tracker/runtime/config (.agent/, ai-hats.yaml, .claude/) lives in gitignored
    # paths and is edited from the MAIN repo by design — gitignored files are not
    # version-controlled source and must NOT be denied (HATS-889 false-positive).
    main, _ = repos
    (main / ".gitignore").write_text(".agent/\n")
    (main / ".agent").mkdir()
    res = _run(main / ".agent" / "card.yaml")  # a trigger-ext (.yaml) in a gitignored dir
    assert res.returncode == 0, res.stderr
    assert _decision(res) == (None, None), f"gitignored yaml must be silent, {res.stdout!r}"


@pytest.mark.integration
def test_non_git_path_is_silent(tmp_path):
    res = _run(tmp_path / "loose.py")  # tmp_path is not a git repo
    assert res.returncode == 0, res.stderr
    assert _decision(res) == (None, None), f"non-git path must be silent, {res.stdout!r}"


@pytest.mark.integration
def test_kill_switch_is_silent(repos):
    main, _ = repos
    res = _run(main / "service.py", env_extra={"AI_HATS_WT_GATE_OFF": "1"})
    assert res.returncode == 0, res.stderr
    assert _decision(res) == (None, None), f"kill switch must silence the gate, {res.stdout!r}"


@pytest.mark.integration
@pytest.mark.parametrize("raw", ["", "not json", "{}", '{"tool_input": {}}'])
def test_malformed_payload_fails_open(raw):
    res = _run(None, raw=raw)
    assert res.returncode == 0, res.stderr
    assert _decision(res) == (None, None), f"malformed payload must fail open, {res.stdout!r}"


# --- HATS-959: the gate must scope to the SESSION's own repo (payload `cwd`) -----------


@pytest.fixture
def foreign(tmp_path):
    """A second, unrelated main checkout (e.g. ~/dotfiles) with a tracked config file."""
    repo = tmp_path / "foreign"
    (repo / ".claude").mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.io")
    _git(repo, "config", "user.name", "t")
    (repo / ".claude" / "settings.json").write_text("{}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


@pytest.mark.integration
def test_file_in_foreign_repo_is_silent(repos, foreign):
    # The reported bug: a session in project A edits a tracked config in an unrelated
    # repo B. B's file is a main-checkout, trigger-ext, non-gitignored file, so the
    # pre-HATS-959 gate denied it. With cwd scoping it is out of project -> silent.
    main, _ = repos
    res = _run(foreign / ".claude" / "settings.json", cwd=main)
    assert res.returncode == 0, res.stderr
    assert _decision(res) == (None, None), f"foreign-repo edit must be silent, {res.stdout!r}"


@pytest.mark.integration
def test_same_repo_main_is_denied_with_cwd(repos):
    # Primary protection preserved: cwd and file share the session repo -> deny.
    main, _ = repos
    res = _run(main / "service.py", cwd=main)
    assert res.returncode == 0, res.stderr
    assert _decision(res)[0] == "deny", f"same-repo main edit should deny, {res.stdout!r}"


@pytest.mark.integration
def test_worktree_session_editing_main_is_denied(repos):
    # cwd inside a linked worktree, file in that repo's MAIN checkout: same
    # git-common-dir -> still the collision case HATS-526 guards -> deny.
    main, linked = repos
    res = _run(main / "service.py", cwd=linked)
    assert res.returncode == 0, res.stderr
    assert _decision(res)[0] == "deny", f"worktree->main edit should deny, {res.stdout!r}"


@pytest.mark.integration
def test_missing_cwd_falls_back_to_old_behavior(repos):
    # cwd absent: cannot scope -> fall back to location-only deny (R4).
    main, _ = repos
    res = _run(main / "service.py")  # no cwd in payload
    assert res.returncode == 0, res.stderr
    assert _decision(res)[0] == "deny", f"no-cwd main edit should deny, {res.stdout!r}"


@pytest.mark.integration
def test_non_git_cwd_falls_back_to_old_behavior(repos, tmp_path):
    # cwd present but not a git repo: session repo unresolvable -> old deny (R4).
    main, _ = repos
    res = _run(main / "service.py", cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert _decision(res)[0] == "deny", f"non-git cwd main edit should deny, {res.stdout!r}"
