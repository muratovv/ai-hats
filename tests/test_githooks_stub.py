"""The installed git-hook stub (HATS-1337).

`.githooks/<event>` is written once and never re-installed, so everything it
contains is a contract with projects composed years apart. These tests hold that
contract narrow: the stub bootstraps an interpreter, delegates, and fails open —
and carries no knowledge of gates, order, stdin or chaining, all of which live in
the package where they can change freely.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

STUB = Path(__file__).parent.parent / "src" / "ai_hats" / "templates" / "githooks" / "dispatcher.sh"

#: The frozen delegation target. Renaming the module disables git gates in every
#: project already composed — the stub there still names the old path.
FROZEN_ENTRY_POINT = "ai_hats.cli.githooks_hook"


def _project(tmp_path: Path, event: str = "pre-commit") -> Path:
    project = tmp_path / "proj"
    (project / ".githooks").mkdir(parents=True)
    hook = project / ".githooks" / event
    hook.write_bytes(STUB.read_bytes())
    hook.chmod(0o755)
    return project


def _stub_interpreter(project: Path, script: str) -> Path:
    python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(script)
    python.chmod(0o755)
    return python


def _run(project: Path, event: str = "pre-commit", *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    return subprocess.run(
        ["/bin/bash", str(project / ".githooks" / event), *args],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
    )


# ----- the contract the stub freezes -----


def test_the_entry_point_module_path_is_frozen():
    """A rename here is silent breakage in the field, so it must be loud in CI."""
    assert FROZEN_ENTRY_POINT in STUB.read_text()

    module = Path(*FROZEN_ENTRY_POINT.split(".")).with_suffix(".py")
    assert (Path(__file__).parent.parent / "src" / module).is_file(), (
        f"{FROZEN_ENTRY_POINT} is named by every installed hook — it may not move"
    )


def test_the_stub_delegates_and_carries_no_hook_logic():
    """Anything that will change must live in the package, not in this file."""
    body = STUB.read_text()

    assert f"from {FROZEN_ENTRY_POINT} import main" in body
    for leaked in (
        "bypass_journal",  # journal wiring — package
        ".d/*",  # drop-in iteration — package
        "previousHooksPath",  # chaining — package
        "pre-receive",  # the stdin-protocol event list — package
        "AI_HATS_HOOK_EVENT",  # env contract with gates — package
    ):
        assert leaked not in body, (
            f"'{leaked}' is hook logic; it belongs in the package so it can change "
            "without re-installing every project's hook"
        )


def test_a_subcommand_invocation_is_never_reintroduced():
    """An unregistered subcommand reads as a bare prompt and launches a provider
    session (`_PassthroughGroup`) — from a git hook that rewrites the running
    file. Measured, not hypothesised."""
    assert "-m ai_hats " not in STUB.read_text()


def test_an_ai_hats_without_the_entry_point_fails_open(tmp_path: Path):
    """Version skew must SKIP the gates, never refuse the event.

    A real interpreter with no ai_hats is the version-skew shape. Run with `-m`
    it exits 1 for "No module named", and under `exec` that 1 becomes the hook's
    verdict — a commit blocked by the very upgrade this stub exists to survive.
    The stub imports and catches instead.
    """
    project = _project(tmp_path)
    system_python = shutil.which("python3")
    assert system_python, "need a system python3 to play the older install"
    _stub_interpreter(
        project,
        f'#!/usr/bin/env bash\nexec env -u PYTHONPATH {system_python} "$@"\n',
    )

    result = _run(project)

    assert result.returncode == 0, (
        f"an ai-hats that cannot serve the entry point must not wedge the commit:\n{result.stderr}"
    )
    assert "fail-open" in result.stderr, result.stderr


# ----- the two jobs it cannot delegate -----


def test_no_interpreter_means_the_commit_still_lands(tmp_path: Path):
    project = _project(tmp_path)

    result = _run(project)

    assert result.returncode == 0, result.stderr
    assert "fail-open" in result.stderr


def test_it_delegates_the_event_project_and_hook_args(tmp_path: Path):
    """Everything the package needs to do its job, and nothing more."""
    project = _project(tmp_path, "post-checkout")
    seen = project / "argv.txt"
    # NUL-separated: the bootstrap it is handed is multi-line, so newline
    # separation would shred it across "arguments".
    _stub_interpreter(
        project,
        f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "{seen}"\nexit 0\n',
    )

    result = _run(project, "post-checkout", "old-sha", "new-sha", "1")

    assert result.returncode == 0, result.stderr
    argv = seen.read_text().split("\0")[:-1]
    assert argv[0] == "-c"
    assert FROZEN_ENTRY_POINT in argv[1], "the bootstrap must import the frozen entry point"
    # Everything after the bootstrap script is what the package is handed.
    assert argv[2:] == [
        "post-checkout",
        "--project-dir",
        str(project.resolve()),
        "--githooks-dir",
        str((project / ".githooks").resolve()),
        "--",
        "old-sha",
        "new-sha",
        "1",
    ]


def test_the_delegate_exit_code_reaches_git(tmp_path: Path):
    """A refusing gate must block the event; the stub is `exec`, not a wrapper."""
    project = _project(tmp_path)
    _stub_interpreter(project, "#!/usr/bin/env bash\nexit 7\n")

    assert _run(project).returncode == 7


def test_a_leaked_ai_hats_dir_from_another_project_is_ignored(tmp_path: Path):
    """A session elsewhere exports AI_HATS_DIR; honouring it would send the stub
    hunting for an interpreter under a foreign checkout (HATS-897)."""
    project = _project(tmp_path)
    foreign = tmp_path / "foreign" / ".agent" / "ai-hats" / ".venv" / "bin"
    foreign.mkdir(parents=True)
    (foreign / "python").write_text("#!/usr/bin/env bash\nexit 3\n")
    (foreign / "python").chmod(0o755)

    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    env["AI_HATS_DIR"] = str(tmp_path / "foreign" / ".agent" / "ai-hats")
    result = subprocess.run(
        ["/bin/bash", str(project / ".githooks" / "pre-commit")],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, "the foreign interpreter must not be used"
    assert "fail-open" in result.stderr
