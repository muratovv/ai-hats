"""e2e (HATS-1932)

flow:   a developer guessing a subcommand that does not exist
cmds:
    ai-hats githooks --help  # no-resolve: pins the refusal, no session is launched
    ai-hats task list        # no-resolve: the retired backlog CLI names `rack`
    ai-hats -- githooks      # no-resolve: `--` still reaches the provider path
expect: exit 2 with a message naming the real invocation, and no new session directory
why:    without the guard the token reaches the provider as a prompt — five field
        sessions launched claude, printed claude's usage and were SIGTERM'd 6 s
        later, leaving a dead session directory instead of a CLI error
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The guard's fingerprint — any refusal it renders carries this line.
GUARD_MARKER = "list the real subcommands"


def _run_ai_hats(cwd: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run ``python -m ai_hats <args>`` against the current checkout."""
    env = os.environ.copy()
    from _helpers.env import checkout_pythonpath

    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, "-m", "ai_hats", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A tmp cwd whose runs dir already exists, so 'no new session' is a real claim."""
    (tmp_path / ".agent" / "ai-hats" / "sessions" / "runs").mkdir(parents=True)
    return tmp_path


def _session_dirs(project: Path) -> set[str]:
    from _helpers.sessions import snapshot_session_dirs

    return set(snapshot_session_dirs(project).existing_names)


def test_absence_assertion_can_fail(project: Path) -> None:
    """Positive control: the snapshot notices a session directory when one appears.

    Without this, every "no new session" assertion below would also pass against a
    wrong runs path or a broken helper — the exact way this e2e could go vacuous.
    """
    before = _session_dirs(project)
    (project / ".agent" / "ai-hats" / "sessions" / "runs" / "session_probe").mkdir()
    assert _session_dirs(project) - before == {"session_probe"}


def test_reserved_word_refused_and_no_session_created(project: Path) -> None:
    """The reported invocation: refused, explained, and nothing launched."""
    before = _session_dirs(project)
    r = _run_ai_hats(project, "githooks", "--help")
    out = r.stdout + r.stderr

    assert r.returncode == 2, f"expected a usage error, got {r.returncode}\n{out}"
    assert "githooks" in out
    assert "python -m ai_hats.cli.githooks_hook" in out, "must name where the capability went"
    assert "ai-hats -- githooks" in out, "must name the escape for a literal prompt"
    assert _session_dirs(project) == before, "a refused invocation must launch no session"


def test_retired_backlog_cli_points_at_rack(project: Path) -> None:
    """`ai-hats task list` used to launch a session with the prompt 'task list'."""
    r = _run_ai_hats(project, "task", "list")
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "rack" in out


def test_double_dash_is_not_refused(project: Path) -> None:
    """The escape reaches the provider path — whatever happens next is not the guard."""
    r = _run_ai_hats(project, "--", "githooks")
    out = r.stdout + r.stderr
    assert GUARD_MARKER not in out, f"`--` must bypass the guard entirely\n{out}"


def test_registered_subcommand_still_renders_its_own_help(project: Path) -> None:
    """The guard must not shadow a real subcommand's `--help` (regression: it did)."""
    r = _run_ai_hats(project, "wt", "--help")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert GUARD_MARKER not in out
    assert "worktree" in out.lower()
