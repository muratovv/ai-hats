"""HATS-823 — worktree hook execution contract (ADR-0012 D7).

stdin closed, cwd = project_dir, AI_HATS_* env, bounded timeout, missing /
non-executable / non-zero / timeout all reported as a failed HookOutcome (the
caller owns fail-closed vs warn-continue); SIGINT is NOT swallowed.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.worktree_hooks import (
    WT_HOOK_TIMEOUT_S,
    resolve_hook_timeout,
    run_worktree_hook,
)


def _script(p: Path, body: str) -> Path:
    p.write_text("#!/usr/bin/env bash\nset -e\n" + body)
    p.chmod(0o755)
    return p


def _run(script: Path, tmp_path: Path, **kw):
    return run_worktree_hook(
        script,
        event=kw.get("event", "merge"),
        worktree_path=kw.get("wt", tmp_path / "wt"),
        project_dir=kw.get("proj", tmp_path),
        branch_name=kw.get("branch", "task/x"),
        timeout=kw.get("timeout"),
        log_path=kw.get("log"),
    )


def test_success(tmp_path):
    out = _run(_script(tmp_path / "ok.sh", "exit 0\n"), tmp_path)
    assert out.ok and out.exit_code == 0


def test_nonzero_exit(tmp_path):
    out = _run(_script(tmp_path / "bad.sh", "exit 3\n"), tmp_path)
    assert not out.ok and out.exit_code == 3


def test_missing_script(tmp_path):
    out = _run(tmp_path / "nope.sh", tmp_path)
    assert not out.ok and "missing" in out.reason


def test_non_executable(tmp_path):
    p = tmp_path / "ne.sh"
    p.write_text("#!/usr/bin/env bash\nexit 0\n")  # no +x
    out = _run(p, tmp_path)
    assert not out.ok and "executable" in out.reason


def test_timeout(tmp_path):
    out = _run(_script(tmp_path / "slow.sh", "sleep 5\n"), tmp_path, timeout=0.4)
    assert not out.ok and "tim" in out.reason.lower()


def test_stdin_closed_does_not_hang(tmp_path):
    # `read` gets EOF immediately (DEVNULL) instead of blocking forever.
    out = _run(_script(tmp_path / "rd.sh", "read x || true\nexit 0\n"), tmp_path, timeout=3)
    assert out.ok


def test_env_and_cwd(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    res = tmp_path / "res.txt"
    s = _script(
        tmp_path / "env.sh",
        f'printf "%s|%s|%s|%s|%s" '
        f'"$AI_HATS_EVENT" "$AI_HATS_BRANCH_NAME" "$AI_HATS_WORKTREE_PATH" '
        f'"$AI_HATS_PROJECT_DIR" "$(pwd -P)" > "{res}"\n',
    )
    out = _run(
        s, tmp_path, event="discard", branch="task/y",
        wt=tmp_path / "mywt", proj=proj,
    )
    assert out.ok
    parts = res.read_text().split("|")
    assert parts[0] == "discard"
    assert parts[1] == "task/y"
    assert parts[2] == str(tmp_path / "mywt")
    assert parts[3] == str(proj)
    assert Path(parts[4]).resolve() == proj.resolve()  # cwd == project_dir


def test_log_captures_output(tmp_path):
    log = tmp_path / "logs" / "hook.log"
    out = _run(_script(tmp_path / "o.sh", "echo hello-from-hook\nexit 0\n"), tmp_path, log=log)
    assert out.ok
    assert "hello-from-hook" in log.read_text()


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("AI_HATS_WT_HOOK_TIMEOUT_S", "12.5")
    assert resolve_hook_timeout() == 12.5
    monkeypatch.setenv("AI_HATS_WT_HOOK_TIMEOUT_S", "garbage")
    assert resolve_hook_timeout() == WT_HOOK_TIMEOUT_S


def test_default_timeout_under_lifecycle_lock_budget():
    # D7: a hook must time out before a peer waiting on the lifecycle lock
    # would mis-blame a concurrent op (HATS-711 class).
    from ai_hats.worktree_locks import LIFECYCLE_LOCK_TIMEOUT

    assert WT_HOOK_TIMEOUT_S < LIFECYCLE_LOCK_TIMEOUT
