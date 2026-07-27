"""HATS-1253 — permission contracts over the COMPOSED PreToolUse chain.

``safety_gate.check_git`` blanket-denied any command whose tokens contained
``push``, overriding the graduated consent ``pre_bash_shared_state_guard.sh``
already implemented; a deny is binary, so the cruder gate won while a
single-hook test asserting the opposite stayed green. Such tests structurally
cannot catch this — these run the whole chain (``dev_rule_e2e_gate``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _helpers.hook_chain import build_session_settings, run_chain  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SHARED_ACK = "AI_HATS_SHARED_STATE_ACK"
DESTRUCTIVE_ACK = "AI_HATS_DESTRUCTIVE_ACK"


@pytest.fixture(scope="module")
def hooked_project(shared_launcher, tmp_path_factory):
    """A real project whose composed session wires the Bash hook chain."""
    import subprocess

    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("hook-chain-home"))

    project = tmp_path_factory.mktemp("hook-chain-proj")
    result = subprocess.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(f"self init failed:\n{result.stdout}\n{result.stderr}")
    return project, env, build_session_settings(project)


# --- The approved-push handshake (R-1) -------------------------------------


@pytest.mark.integration
def test_approved_push_completes_with_ack(hooked_project):
    """The documented handshake must actually complete.

    Fail-under-revert: restore ``check_git`` and this denies again — which is
    exactly the reported bug (supervisor approved, agent still could not push).
    """
    project, env, settings = hooked_project
    verdict = run_chain(project, "git push origin master", settings=settings, env=env, ack=SHARED_ACK)
    assert not verdict.denied, (
        f"an approved push must pass the chain with {SHARED_ACK}=1; got {verdict}"
    )


@pytest.mark.integration
def test_unapproved_push_is_denied_and_names_its_hatch(hooked_project):
    """An unapproved push is denied, and the denial says how to proceed.

    The second half is the deny-names-its-hatch invariant (P4): ``check_git``
    denied with "requires explicit permission" and named no flag, leaving the
    agent nowhere to go but blunt instruments.
    """
    project, env, settings = hooked_project
    verdict = run_chain(project, "git push origin master", settings=settings, env=env)
    assert verdict.denied, f"an unapproved push must be denied; got {verdict}"
    assert verdict.names_ack_flag, (
        f"denial must name the consent flag that unblocks it; got {verdict}"
    )


# --- No false denials (R-3) -------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("git stash push -- src/foo.py", id="stash-push-is-local"),
        pytest.param("git push --dry-run origin master", id="dry-run-writes-nothing"),
        pytest.param("git log -S 'git push origin' --oneline", id="read-only-history-search"),
        pytest.param("git status", id="plain-read-only"),
    ],
)
@pytest.mark.integration
def test_non_mutating_git_commands_pass(hooked_project, command):
    """Commands that merely mention the word must not be denied (R-3/R5)."""
    project, env, settings = hooked_project
    verdict = run_chain(project, command, settings=settings, env=env)
    assert not verdict.denied, f"{command!r} is non-mutating but was blocked; got {verdict}"


# --- The irreversible class must NOT regress -------------------------------


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("git push --force origin master", id="force-push"),
        pytest.param("gh pr merge 42 --merge --delete-branch", id="pr-merge"),
    ],
)
@pytest.mark.integration
def test_irreversible_still_denied_without_ack(hooked_project, command):
    """Loosening regular push must not loosen the irreversible class."""
    project, env, settings = hooked_project
    verdict = run_chain(project, command, settings=settings, env=env)
    assert verdict.denied, f"{command!r} is irreversible and must stay denied; got {verdict}"


# --- The rm policy (R-6/R-7) ------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("rm -f /tmp/hats-scratch.txt", id="own-temp-file"),
        pytest.param("rm -rf /tmp/hats-workdir", id="own-temp-dir"),
        pytest.param("rm -rf ./build", id="build-artefacts"),
    ],
)
@pytest.mark.integration
def test_cleanup_is_allowed(hooked_project, command):
    """``global_rule_resource_hygiene`` MANDATES this cleanup (R-7).

    A hook may not deny what a rule obliges the agent to do — that wedge is the
    reported symptom (agents stopped clearing /tmp).
    """
    project, env, settings = hooked_project
    verdict = run_chain(project, command, settings=settings, env=env)
    assert not verdict.denied, f"{command!r} is mandated cleanup but was blocked; got {verdict}"


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("rm -f data/app.sqlite3", id="sqlite-db"),
        pytest.param("rm .env", id="dotenv"),
        pytest.param("rm -rf volumes/", id="volumes-dir"),
        pytest.param("rm -f terraform.tfstate", id="tfstate"),
    ],
)
@pytest.mark.integration
def test_protected_paths_deny_but_ack_opens(hooked_project, command):
    """The paths ``global_rule_destructive_actions`` names: deny, then ack opens."""
    project, env, settings = hooked_project

    bare = run_chain(project, command, settings=settings, env=env)
    assert bare.denied, f"{command!r} targets protected data and must be denied; got {bare}"
    assert bare.names_ack_flag, f"denial must name its consent flag; got {bare}"

    acked = run_chain(project, command, settings=settings, env=env, ack=DESTRUCTIVE_ACK)
    assert not acked.denied, (
        f"{command!r} must proceed once approved via {DESTRUCTIVE_ACK}=1; got {acked}"
    )


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("rm -rf /", id="filesystem-root"),
        pytest.param("rm -rf $HOME", id="home-dir"),
    ],
)
@pytest.mark.integration
def test_catastrophic_targets_deny_even_with_ack(hooked_project, command):
    """The no-hatch class. The whole point of loosening ``rm`` is that the
    loosening stops short of here — asserted so no future refactor can widen
    the allow branch quietly."""
    project, env, settings = hooked_project
    for ack in (None, DESTRUCTIVE_ACK, SHARED_ACK):
        verdict = run_chain(project, command, settings=settings, env=env, ack=ack)
        assert verdict.denied, (
            f"{command!r} must be denied even with ack={ack}; got {verdict}"
        )


# --- Binaries that are not data-destructive --------------------------------


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("truncate -s 0 /tmp/hats.log", id="truncate"),
        pytest.param("chown me /tmp/hats-file", id="chown"),
    ],
)
@pytest.mark.integration
def test_non_destructive_binaries_pass(hooked_project, command):
    """``chown``/``truncate`` destroy no data in the sense the rule protects."""
    project, env, settings = hooked_project
    verdict = run_chain(project, command, settings=settings, env=env)
    assert not verdict.denied, f"{command!r} should not be gated; got {verdict}"


# --- Invariants that guard FUTURE denies -----------------------------------

#: Denials for which no consent flag exists, by design.
NO_HATCH = ("rm -rf /", "rm -rf $HOME", "mkfs.ext4 /dev/sda1")

#: A broad sweep. Rows here are not asserted allow/deny — the invariant is only
#: that whatever is denied explains how to proceed.
SWEEP = NO_HATCH + (
    "git push origin master",
    "git push --force origin master",
    "gh pr merge 42 --merge",
    "rm -f data/app.sqlite3",
    "rm .env",
    "rm -rf volumes/",
    "dd if=/dev/zero of=/dev/sda",
    "sed -i 's/a/b/' file.txt",
    "psql -c 'drop table users'",
)


@pytest.mark.parametrize("command", SWEEP)
@pytest.mark.integration
def test_every_deny_names_its_hatch(hooked_project, command):
    """A deny must name the flag that opens it, or be a known no-hatch case.

    The guard against the HATS-1113 shape: a deny with nowhere to go leaves the
    agent only blunt instruments, which is what graduated consent exists to
    avoid. Applies to denies added later, not just the ones fixed here.
    """
    project, env, settings = hooked_project
    verdict = run_chain(project, command, settings=settings, env=env)
    if not verdict.denied:
        return
    if command in NO_HATCH:
        assert not verdict.names_ack_flag, (
            f"{command!r} must not advertise a flag that cannot open it; got {verdict}"
        )
        return
    assert verdict.names_ack_flag, (
        f"{command!r} was denied without naming a consent flag; got {verdict}"
    )


@pytest.mark.integration
def test_mkfs_still_denied(hooked_project):
    """``mkfs`` has no legitimate agent use and stays on the blocked list."""
    project, env, settings = hooked_project
    verdict = run_chain(project, "mkfs.ext4 /dev/sda1", settings=settings, env=env)
    assert verdict.denied, f"mkfs must stay denied; got {verdict}"
