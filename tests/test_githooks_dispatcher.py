"""The dispatcher template, driven as real bash (HATS-1337, ADR-0020 D3).

The dispatcher never imports ai-hats — it spawns an interpreter and parses that
process's stdout. So every case here runs the real script under the real shell
with a STUB interpreter, and asserts on what a `git commit` would observe.

Fail-open is the property under test more than any single case: an absent,
broken or mid-update ai-hats must never wedge a commit.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DISPATCHER = (
    Path(__file__).parent.parent / "src" / "ai_hats" / "templates" / "githooks" / "dispatcher.sh"
)


def _project(tmp_path: Path, event: str = "pre-commit") -> Path:
    """A git repo whose `.githooks/<event>` is the real dispatcher template."""
    project = tmp_path / "proj"
    (project / ".githooks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    hook = project / ".githooks" / event
    hook.write_bytes(DISPATCHER.read_bytes())
    hook.chmod(0o755)
    return project


def _stub_interpreter(project: Path, script: str) -> None:
    """Install `.agent/ai-hats/.venv/bin/python` — resolver tier 4 (legacy venv)."""
    python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(script)
    python.chmod(0o755)


def _gate(project: Path, name: str, body: str = "exit 0") -> Path:
    """A gate script living OUTSIDE .githooks/, as a library gate does."""
    gate = project / "lib" / name
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(f"#!/usr/bin/env bash\n{body}\n")
    gate.chmod(0o755)
    return gate


def _run(project: Path, event: str = "pre-commit", stdin: str = "") -> subprocess.CompletedProcess:
    """Run the hook the way git does, with the ambient session pin scrubbed."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    return subprocess.run(
        ["/bin/bash", str(project / ".githooks" / event)],
        cwd=str(project),
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
    )


# ----- fail-open: the property a wedged commit would violate -----


def test_no_ai_hats_at_all_lets_the_commit_through(tmp_path: Path) -> None:
    """Nothing installed: exit 0 and say so. This is the Z2 regression."""
    project = _project(tmp_path)

    result = _run(project)

    assert result.returncode == 0, result.stderr
    assert "SKIPPED (fail-open)" in result.stderr
    assert "no usable ai-hats install" in result.stderr


def test_a_pre_1337_degraded_install_no_longer_blocks(tmp_path: Path) -> None:
    """Z2, the regression this card exists for.

    The old dispatcher read `.ai-hats-manifest` and refused the event outright
    when a managed `<event>.d/` entry was missing or lost its +x — prescribing
    `ai-hats self init`, which needs exactly the binary that is gone. That state
    is still on disk in every project composed before this change (until the
    one-time cleanup runs), and a commit may fire first. It must pass.
    """
    project = _project(tmp_path)
    (project / ".githooks" / ".ai-hats-manifest").write_text(
        "# ai-hats-owner: git-hooks\n"
        "36d843e31b66  pre-commit\n"
        "2f8f63cc7334  pre-commit.d/git-mastery-pre-commit-privacy.sh\n"
    )
    (project / ".githooks" / "pre-commit.d").mkdir()  # the listed entry is NOT there

    result = _run(project)

    assert result.returncode == 0, (
        f"a degraded pre-1337 install must not wedge the commit; stderr={result.stderr!r}"
    )
    assert "corrupt" not in result.stderr
    assert "self init" not in result.stderr


def test_the_dispatcher_spawns_a_module_never_a_cli_subcommand() -> None:
    """An unknown SUBCOMMAND would launch a provider session; a module cannot.

    `ai-hats`'s top-level group treats an unregistered subcommand as a bare
    positional prompt and starts a wrapped provider session (`_PassthroughGroup`,
    HATS-1202). Spawned from a git hook against an older ai-hats — exactly the
    version skew the orchestrator is built to survive — that turns `git commit`
    into an agent session which re-materializes the project and overwrites the
    running hook mid-execution. Measured, not hypothesised: it clobbered a v2
    dispatcher with a v1 template while bash was still reading it.
    """
    body = DISPATCHER.read_text()

    assert "-m ai_hats.cli.githooks_main" in body
    assert "-m ai_hats githooks" not in body, (
        "a subcommand invocation reopens the passthrough-session hole"
    )


def test_an_ai_hats_without_the_resolver_module_fails_open(tmp_path: Path) -> None:
    """The version-skew path, end to end: old install → gates skipped, exit 0."""
    project = _project(tmp_path)
    # An interpreter that behaves like a real python with no such module.
    _stub_interpreter(
        project,
        "#!/usr/bin/env bash\n"
        'echo "/usr/bin/python: No module named ai_hats.cli.githooks_main" >&2\n'
        "exit 1\n",
    )

    result = _run(project)

    assert result.returncode == 0, result.stderr
    assert "could not resolve" in result.stderr


def test_a_resolver_that_fails_lets_the_commit_through(tmp_path: Path) -> None:
    """An installed but broken ai-hats is the same posture as an absent one."""
    project = _project(tmp_path)
    _stub_interpreter(project, "#!/usr/bin/env bash\necho 'boom' >&2\nexit 1\n")

    result = _run(project)

    assert result.returncode == 0, result.stderr
    assert "could not resolve" in result.stderr
    assert "boom" in result.stderr, "the resolver's own reason must reach the operator"


# ----- the resolved chain -----


def test_gates_run_in_the_order_the_resolver_printed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    order = project / "order.txt"
    for name in ("first.sh", "second.sh", "third.sh"):
        _gate(project, name, body=f'echo {name} >> "{order}"')
    lib = project / "lib"
    _stub_interpreter(
        project,
        "#!/usr/bin/env bash\n"
        f'printf "gate\\t{lib}/first.sh\\ngate\\t{lib}/second.sh\\ngate\\t{lib}/third.sh\\n"\n',
    )

    result = _run(project)

    assert result.returncode == 0, result.stderr
    assert order.read_text().split() == ["first.sh", "second.sh", "third.sh"]


def test_the_first_failing_gate_aborts_with_its_own_exit_code(tmp_path: Path) -> None:
    project = _project(tmp_path)
    ran = project / "ran.txt"
    _gate(project, "ok.sh", body=f'echo ok >> "{ran}"')
    _gate(project, "refuse.sh", body="exit 7")
    _gate(project, "never.sh", body=f'echo never >> "{ran}"')
    lib = project / "lib"
    _stub_interpreter(
        project,
        "#!/usr/bin/env bash\n"
        f'printf "gate\\t{lib}/ok.sh\\ngate\\t{lib}/refuse.sh\\ngate\\t{lib}/never.sh\\n"\n',
    )

    result = _run(project)

    assert result.returncode == 7, result.stderr
    assert ran.read_text().split() == ["ok"], "the chain must stop at the refusal"


def test_the_event_and_journal_reach_the_gate_as_env(tmp_path: Path) -> None:
    """A gate's own $0 is its library path, so both facts must arrive as env."""
    project = _project(tmp_path)
    seen = project / "seen.txt"
    _gate(
        project,
        "probe.sh",
        body=f'echo "$AI_HATS_HOOK_EVENT $AI_HATS_BYPASS_JOURNAL" > "{seen}"',
    )
    lib = project / "lib"
    _stub_interpreter(
        project,
        f'#!/usr/bin/env bash\nprintf "journal\\t{lib}/journal.sh\\ngate\\t{lib}/probe.sh\\n"\n',
    )

    result = _run(project)

    assert result.returncode == 0, result.stderr
    assert seen.read_text().strip() == f"pre-commit {lib}/journal.sh"


# ----- what the project owns, ai-hats does not touch -----


def test_a_user_dropin_still_runs_after_the_resolved_gates(tmp_path: Path) -> None:
    """`<event>.d/` is read, never written (R8) — the affordance survives."""
    project = _project(tmp_path)
    order = project / "order.txt"
    _gate(project, "gate.sh", body=f'echo gate >> "{order}"')
    dropin = project / ".githooks" / "pre-commit.d" / "zz-user.sh"
    dropin.parent.mkdir(parents=True)
    dropin.write_text(f'#!/usr/bin/env bash\necho dropin >> "{order}"\n')
    dropin.chmod(0o755)
    lib = project / "lib"
    _stub_interpreter(project, f'#!/usr/bin/env bash\nprintf "gate\\t{lib}/gate.sh\\n"\n')

    result = _run(project)

    assert result.returncode == 0, result.stderr
    assert order.read_text().split() == ["gate", "dropin"]


def test_a_user_dropin_runs_even_with_no_ai_hats(tmp_path: Path) -> None:
    """The project's own hooks do not depend on ai-hats being installed."""
    project = _project(tmp_path)
    marker = project / "ran.txt"
    dropin = project / ".githooks" / "pre-commit.d" / "zz-user.sh"
    dropin.parent.mkdir(parents=True)
    dropin.write_text(f'#!/usr/bin/env bash\necho dropin > "{marker}"\n')
    dropin.chmod(0o755)

    result = _run(project)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().strip() == "dropin"


# ----- stdin fan-out (HATS-654) -----


@pytest.mark.parametrize("consumer_first", [True, False])
def test_every_pre_push_gate_sees_the_ref_protocol(tmp_path: Path, consumer_first: bool) -> None:
    """A gate that drains stdin must not blind the next one (HATS-654).

    Parametrized on ordering so the pass cannot be an artifact of the draining
    gate happening to run last.
    """
    project = _project(tmp_path, event="pre-push")
    seen = project / "seen.txt"
    _gate(project, "drainer.sh", body="cat > /dev/null")
    _gate(project, "reader.sh", body=f'cat >> "{seen}"')
    lib = project / "lib"
    first, second = ("drainer.sh", "reader.sh") if consumer_first else ("reader.sh", "drainer.sh")
    _stub_interpreter(
        project,
        f'#!/usr/bin/env bash\nprintf "gate\\t{lib}/{first}\\ngate\\t{lib}/{second}\\n"\n',
    )

    protocol = "refs/heads/master abc123 refs/heads/master def456\n"
    result = _run(project, event="pre-push", stdin=protocol)

    assert result.returncode == 0, result.stderr
    assert seen.read_text() == protocol
