"""The git-hook chain runner (HATS-1337).

Behaviour that used to live in the installed bash dispatcher now lives here, so
it can change without re-installing anything. Every case runs real scripts and
asserts on what a `git commit` would observe.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats.githooks_run import run_chain


def _repo(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".githooks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    return project


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)
    return path


def _run(project: Path, event: str = "pre-commit", **kw) -> int:
    return run_chain(
        event=event,
        project_dir=project,
        githooks_dir=project / ".githooks",
        gates=kw.pop("gates", []),
        journal=kw.pop("journal", None),
        argv=kw.pop("argv", []),
    )


def test_nothing_to_run_is_a_pass(tmp_path: Path):
    assert _run(_repo(tmp_path)) == 0


def test_gates_run_in_the_order_given(tmp_path: Path):
    """The resolver fixes the order (the retired copies' `<skill>-<basename>`);
    the runner must not reorder it."""
    project = _repo(tmp_path)
    order = project / "order.txt"
    gates = [
        _script(project / "lib" / n, f'echo {n} >> "{order}"')
        for n in ("first.sh", "second.sh", "third.sh")
    ]

    assert _run(project, gates=gates) == 0
    assert order.read_text().split() == ["first.sh", "second.sh", "third.sh"]


def test_the_first_refusal_aborts_with_its_own_code(tmp_path: Path):
    project = _repo(tmp_path)
    ran = project / "ran.txt"
    gates = [
        _script(project / "lib" / "ok.sh", f'echo ok >> "{ran}"'),
        _script(project / "lib" / "refuse.sh", "exit 7"),
        _script(project / "lib" / "never.sh", f'echo never >> "{ran}"'),
    ]

    assert _run(project, gates=gates) == 7
    assert ran.read_text().split() == ["ok"]


def test_the_event_and_journal_reach_the_gate_as_env(tmp_path: Path):
    """A gate's own $0 is its library path, so both must arrive as env."""
    project = _repo(tmp_path)
    seen = project / "seen.txt"
    gate = _script(
        project / "lib" / "probe.sh",
        f'echo "$AI_HATS_HOOK_EVENT $AI_HATS_BYPASS_JOURNAL" > "{seen}"',
    )
    journal = _script(project / "lib" / "journal.sh", "true")

    assert _run(project, gates=[gate], journal=journal) == 0
    assert seen.read_text().strip() == f"pre-commit {journal}"


def test_hook_arguments_are_forwarded(tmp_path: Path):
    project = _repo(tmp_path)
    seen = project / "argv.txt"
    gate = _script(project / "lib" / "probe.sh", f'printf "%s\\n" "$@" > "{seen}"')

    assert _run(project, "post-checkout", gates=[gate], argv=["old", "new", "1"]) == 0
    assert seen.read_text().split() == ["old", "new", "1"]


# ----- what the project owns -----


def test_a_dropin_runs_after_the_resolved_gates(tmp_path: Path):
    """`<event>.d/` is read, never written by ai-hats (R8)."""
    project = _repo(tmp_path)
    order = project / "order.txt"
    gate = _script(project / "lib" / "gate.sh", f'echo gate >> "{order}"')
    _script(project / ".githooks" / "pre-commit.d" / "zz-user.sh", f'echo dropin >> "{order}"')

    assert _run(project, gates=[gate]) == 0
    assert order.read_text().split() == ["gate", "dropin"]


def test_the_previous_hook_manager_is_chained_last(tmp_path: Path):
    """HATS-999: ai-hats took `core.hooksPath` from husky/simple-git-hooks; the
    hook they still write must keep running."""
    project = _repo(tmp_path)
    order = project / "order.txt"
    gate = _script(project / "lib" / "gate.sh", f'echo gate >> "{order}"')
    _script(project / ".husky" / "pre-commit", f'echo husky >> "{order}"')
    subprocess.run(
        ["git", "config", "ai-hats.previousHooksPath", ".husky"], cwd=str(project), check=True
    )

    assert _run(project, gates=[gate]) == 0
    assert order.read_text().split() == ["gate", "husky"]


def test_a_failing_chained_hook_blocks_the_event(tmp_path: Path):
    project = _repo(tmp_path)
    _script(project / ".husky" / "pre-commit", "exit 3")
    subprocess.run(
        ["git", "config", "ai-hats.previousHooksPath", ".husky"], cwd=str(project), check=True
    )

    assert _run(project) == 3


def test_a_previous_path_pointing_at_us_is_not_chained(tmp_path: Path):
    """A self-pointing record exists in the wild; chaining it would recurse."""
    project = _repo(tmp_path)
    _script(project / ".githooks" / "pre-commit", "exit 9")
    subprocess.run(
        ["git", "config", "ai-hats.previousHooksPath", ".githooks"], cwd=str(project), check=True
    )

    assert _run(project) == 0


def test_the_default_git_hooks_dir_is_chained_without_a_record(tmp_path: Path):
    """simple-git-hooks shape: it writes `.git/hooks/<event>` and sets nothing."""
    project = _repo(tmp_path)
    marker = project / "ran.txt"
    _script(project / ".git" / "hooks" / "pre-commit", f'echo native > "{marker}"')

    assert _run(project) == 0
    assert marker.read_text().strip() == "native"


# ----- stdin fan-out (HATS-654) -----


@pytest.mark.parametrize("drainer_first", [True, False])
def test_every_pre_push_script_sees_the_ref_protocol(
    tmp_path: Path, drainer_first: bool, monkeypatch
):
    """One shared stdin lets the first consumer drain it and blinds the rest.

    Parametrized on order so a pass cannot be an artifact of the draining script
    happening to run last.
    """
    project = _repo(tmp_path)
    seen = project / "seen.txt"
    drainer = _script(project / "lib" / "drainer.sh", "cat > /dev/null")
    reader = _script(project / "lib" / "reader.sh", f'cat >> "{seen}"')
    gates = [drainer, reader] if drainer_first else [reader, drainer]

    protocol = "refs/heads/master abc refs/heads/master def\n"
    stdin_path = tmp_path / "stdin"
    stdin_path.write_text(protocol)
    with stdin_path.open("rb") as fh:
        monkeypatch.setattr("sys.stdin", type("S", (), {"buffer": fh})())
        assert _run(project, "pre-push", gates=gates) == 0

    assert seen.read_text() == protocol


def test_a_stdin_less_event_never_reads_stdin(tmp_path: Path, monkeypatch):
    """Reading on a stdin-less event blocks forever on an open pipe — an agent
    harness leaves one on fd 0."""
    project = _repo(tmp_path)

    class _Exploding:
        @property
        def buffer(self):
            raise AssertionError("pre-commit must never read stdin")

    monkeypatch.setattr("sys.stdin", _Exploding())
    gate = _script(project / "lib" / "gate.sh", "true")

    assert _run(project, "pre-commit", gates=[gate]) == 0


def test_the_runner_does_not_leak_ai_hats_env_into_a_bare_run(tmp_path: Path):
    """No journal resolved → the var must be absent, not empty: the gates'
    `${AI_HATS_BYPASS_JOURNAL:-fallback}` must reach its fallback."""
    project = _repo(tmp_path)
    seen = project / "seen.txt"
    gate = _script(
        project / "lib" / "probe.sh",
        f'echo "[${{AI_HATS_BYPASS_JOURNAL:-unset}}]" > "{seen}"',
    )
    os.environ.pop("AI_HATS_BYPASS_JOURNAL", None)

    assert _run(project, gates=[gate], journal=None) == 0
    assert seen.read_text().strip() == "[unset]"


# ----- HATS-1597: an unrunnable script degrades, it never raises ---------------


def _unrunnable(project: Path, mode: int, *, shebang: bool) -> Path:
    script = project / "lib" / "broken.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/usr/bin/env bash\ntrue\n" if shebang else "true\n")
    script.chmod(mode)
    return script


@pytest.mark.parametrize(
    ("mode", "shebang"), [(0o644, True), (0o755, False)], ids=["non-executable", "no-shebang"]
)
def test_a_script_that_cannot_be_exec_d_does_not_raise(tmp_path, capsys, mode, shebang):
    """`run_chain` reaches execve for drop-ins and the chained hook too — neither
    passes through `resolve_git_gates` — and a mode can change between any check
    and the exec. PermissionError (no x bit) and OSError/ENOEXEC (no shebang)
    both used to surface as a traceback on a human's commit.
    """
    project = _repo(tmp_path)

    assert _run(project, gates=[_unrunnable(project, mode, shebang=shebang)]) == 0
    assert "fail-open" in capsys.readouterr().err, "the skip must be spoken"


def test_an_unrunnable_gate_does_not_stop_the_ones_after_it(tmp_path: Path):
    """Skipping is per-script: one bad mode must not silently disarm the rest."""
    project = _repo(tmp_path)
    broken = _unrunnable(project, 0o644, shebang=True)
    ran = project / "ran.txt"
    good = _script(project / "lib" / "good.sh", f'touch "{ran}"')

    assert _run(project, gates=[broken, good]) == 0
    assert ran.exists(), "a later gate was skipped along with the broken one"


# ---- foreign session pin, dropped for the children (HATS-1613, ADR-0025 D3) ----


def test_a_foreign_pin_is_dropped_and_repinned_for_the_chain(tmp_path, capsys):
    """The delivery window: a stub predating the guard passes the pair through."""
    from ai_hats.githooks_run import _drop_foreign_pin

    project = tmp_path / "mine"
    project.mkdir()
    env = {
        "AI_HATS_PROJECT_DIR": str(tmp_path / "theirs"),
        "AI_HATS_VENV": "/somewhere/else/.venv",
        "AI_HATS_DIR": "/somewhere/else/.agent/ai-hats",
    }

    _drop_foreign_pin(env, project)

    assert "AI_HATS_VENV" not in env
    assert "AI_HATS_DIR" not in env
    assert env["AI_HATS_PROJECT_DIR"] == str(project), "a gate must be told the truth"
    err = capsys.readouterr().err
    assert "self update" in err, "a silent window is the defect this branch exists for"


def test_a_foreign_pin_takes_the_whole_identity_with_it(tmp_path, capsys):
    """Dropping the pin but keeping the envelope leaves the identity TORN.

    The pin is re-pinned to this project while the envelope still names the other
    one, so a gate reading the envelope composes under the FOREIGN session's role
    — HATS-1525's cross-project leak, surviving on the identity axis after the
    venv/dir axes were closed. The identity travels as one unit or not at all.
    """
    from ai_hats.githooks_run import _drop_foreign_pin
    from ai_hats.session_identity import SessionIdentity

    project = tmp_path / "mine"
    project.mkdir()
    theirs = tmp_path / "theirs"
    env = {
        "AI_HATS_PROJECT_DIR": str(theirs),
        **SessionIdentity(
            id="sid-theirs",
            role="judge",
            provider="claude",
            project_dir=theirs,
            session_dir=theirs / "s",
        ).to_env(),
    }

    _drop_foreign_pin(env, project)

    assert SessionIdentity.from_env(env) is None, (
        "a foreign session's identity must not survive into this project's gates"
    )
    assert env["AI_HATS_PROJECT_DIR"] == str(project)


def test_a_matching_pin_is_left_alone(tmp_path, capsys):
    from ai_hats.githooks_run import _drop_foreign_pin

    project = tmp_path / "mine"
    project.mkdir()
    env = {"AI_HATS_PROJECT_DIR": str(project), "AI_HATS_VENV": "/mine/.venv"}

    _drop_foreign_pin(env, project)

    assert env["AI_HATS_VENV"] == "/mine/.venv"
    assert capsys.readouterr().err == ""


def test_no_pin_at_all_is_left_alone(tmp_path, capsys):
    from ai_hats.githooks_run import _drop_foreign_pin

    env = {"AI_HATS_VENV": "/bare/override/.venv"}
    _drop_foreign_pin(env, tmp_path)

    assert env == {"AI_HATS_VENV": "/bare/override/.venv"}, "env-wins survives"
    assert capsys.readouterr().err == ""


def test_the_usual_path_is_a_silent_repin_not_a_warning(tmp_path, capsys):
    """A current stub already unset the pair, so only the pin itself arrives.

    Nothing was dropped, so nothing is announced — but the children must still be
    told this project rather than the one the pin names (HATS-1613 review).
    """
    from ai_hats.githooks_run import _drop_foreign_pin

    project = tmp_path / "mine"
    project.mkdir()
    env = {"AI_HATS_PROJECT_DIR": str(tmp_path / "theirs")}

    _drop_foreign_pin(env, project)

    assert env["AI_HATS_PROJECT_DIR"] == str(project)
    assert capsys.readouterr().err == "", "nothing was dropped, so nothing to announce"
