"""Unit tests for the live git-gate resolver (HATS-1337, ADR-0020 D3).

The dispatcher has no ai-hats process of its own; it spawns one and asks this
resolver which gates the composed role declares for an event. What the retired
flatten-copy used to provide as a side effect — containment and chain order —
is asserted here.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent

from ai_hats.githooks_resolve import resolve_git_gates


def _skill(lib: Path, name: str, *, event: str, scripts: list[str]) -> ResolvedComponent:
    """A skill directory declaring ``scripts`` for ``event``, with the files present."""
    skill_dir = lib / "skills" / name
    (skill_dir / "git_hooks").mkdir(parents=True, exist_ok=True)
    declared = "\n".join(f"      - {s}" for s in scripts)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\nai_hats:\n  git_hooks:\n"
        f"    {event}:\n{declared}\n---\n\n# {name}\n"
    )
    for script in scripts:
        target = skill_dir / script
        if ".." in script:
            continue  # an escaping declaration: never materialize a real file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/usr/bin/env bash\nexit 0\n")
        target.chmod(0o755)
    return ResolvedComponent(name=name, component_type=ComponentKind.SKILL, source_path=skill_dir)


def _result(*skills: ResolvedComponent) -> CompositionResult:
    return CompositionResult(name="r", priorities=[], rules=[], skills=list(skills), injections=[])


def test_gates_resolve_in_the_retired_copy_order(tmp_path: Path) -> None:
    """Order is the lexicographic `<skill>-<basename>` of the copies that used to exist.

    Retiring `<event>.d/<skill>-<basename>` must not silently reorder the chain
    (R10): declaration order and skill order both differ from the answer here.
    """
    lib = tmp_path / "lib"
    zed = _skill(lib, "zed-skill", event="pre-commit", scripts=["git_hooks/aaa.sh"])
    ada = _skill(
        lib,
        "ada-skill",
        event="pre-commit",
        scripts=["git_hooks/zzz.sh", "git_hooks/mmm.sh"],
    )

    resolution = resolve_git_gates(_result(zed, ada), "pre-commit")

    assert [g.sort_key for g in resolution.gates] == [
        "ada-skill-mmm.sh",
        "ada-skill-zzz.sh",
        "zed-skill-aaa.sh",
    ]
    assert resolution.refusals == ()
    assert all(g.path.is_absolute() for g in resolution.gates)


def test_a_declaration_escaping_the_skill_dir_is_refused(tmp_path: Path) -> None:
    """M11 containment: the flatten-copy provided this; it must survive its removal.

    The escaping target is made to EXIST, so a pass here cannot be credited to
    the mere `is_file()` check — only containment can refuse it.
    """
    lib = tmp_path / "lib"
    outsider = tmp_path / "outsider.sh"
    outsider.write_text("#!/usr/bin/env bash\necho pwned\n")
    outsider.chmod(0o755)

    skill = _skill(lib, "evil", event="pre-commit", scripts=["../../../outsider.sh"])
    assert (skill.source_path / "../../../outsider.sh").resolve() == outsider.resolve(), (
        "fixture must actually point at the existing outsider, or the test proves nothing"
    )

    resolution = resolve_git_gates(_result(skill), "pre-commit")

    assert resolution.gates == ()
    assert len(resolution.refusals) == 1
    assert "escapes the skill directory" in resolution.refusals[0]


def test_a_symlink_escaping_the_skill_dir_is_refused(tmp_path: Path) -> None:
    """Containment resolves both sides, so an inside-the-skill symlink out is refused."""
    lib = tmp_path / "lib"
    outsider = tmp_path / "outsider.sh"
    outsider.write_text("#!/usr/bin/env bash\nexit 0\n")
    outsider.chmod(0o755)

    skill = _skill(lib, "s", event="pre-commit", scripts=["git_hooks/link.sh"])
    link = skill.source_path / "git_hooks" / "link.sh"
    link.unlink()
    link.symlink_to(outsider)

    resolution = resolve_git_gates(_result(skill), "pre-commit")

    assert resolution.gates == ()
    assert "escapes the skill directory" in resolution.refusals[0]


def test_a_skill_dir_that_is_itself_a_symlink_still_resolves(tmp_path: Path) -> None:
    """The user library links whole skills out to other checkouts (`~/.ai-hats/skills/*`).

    Containment resolves the skill root too, so those are not refused wrongly —
    the counter-case to the two refusals above.
    """
    real = tmp_path / "elsewhere"
    linked_lib = tmp_path / "lib"
    skill = _skill(real, "commit-style", event="commit-msg", scripts=["git_hooks/id.sh"])

    (linked_lib / "skills").mkdir(parents=True)
    alias = linked_lib / "skills" / "commit-style"
    alias.symlink_to(skill.source_path)

    via_link = ResolvedComponent(
        name="commit-style", component_type=ComponentKind.SKILL, source_path=alias
    )
    resolution = resolve_git_gates(_result(via_link), "commit-msg")

    assert resolution.refusals == ()
    assert [g.sort_key for g in resolution.gates] == ["commit-style-id.sh"]


def test_a_declared_script_that_does_not_exist_is_refused_and_named(tmp_path: Path) -> None:
    """A gate that will not run says so — never a silently shorter chain."""
    lib = tmp_path / "lib"
    skill = _skill(lib, "s", event="pre-commit", scripts=["git_hooks/present.sh"])
    (skill.source_path / "git_hooks" / "present.sh").unlink()

    resolution = resolve_git_gates(_result(skill), "pre-commit")

    assert resolution.gates == ()
    assert "does not exist" in resolution.refusals[0]
    assert "s" in resolution.refusals[0]


def test_other_events_are_not_returned(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    skill = _skill(lib, "s", event="pre-push", scripts=["git_hooks/p.sh"])

    assert resolve_git_gates(_result(skill), "pre-commit").gates == ()
    assert [g.script for g in resolve_git_gates(_result(skill), "pre-push").gates] == [
        "git_hooks/p.sh"
    ]


# ----- the entry point the installed stub delegates to -----


@pytest.mark.integration
def test_the_hook_entry_point_composes_and_runs_the_declared_gate(tmp_path: Path) -> None:
    """The seam the frozen stub reaches: role → gates → the gate actually runs.

    Asserted end to end because everything between the stub and the gate is now
    revisable package code — only the outcome is contractual.
    """
    from ai_hats.cli.githooks_hook import main
    from ai_hats.models import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    project = tmp_path / "project"
    (project / ".githooks").mkdir(parents=True)
    lib = tmp_path / "lib"
    _skill(lib, "hook_skill", event="pre-commit", scripts=["git_hooks/check.sh"])
    marker = project / "ran.txt"
    (lib / "skills" / "hook_skill" / "git_hooks" / "check.sh").write_text(
        f'#!/usr/bin/env bash\necho "$AI_HATS_HOOK_EVENT" > "{marker}"\nexit 0\n'
    )
    (lib / "skills" / "hook_skill" / "git_hooks" / "check.sh").chmod(0o755)

    (lib / "traits" / "trait-base").mkdir(parents=True)
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - hook_skill\ninjection: Base.\n"
    )
    (lib / "roles" / "test-role").mkdir(parents=True)
    (lib / "roles" / "test-role" / "config.yaml").write_text(
        "name: test-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: Role.\n"
    )
    ProjectConfig(provider="agy", library_paths=[str(lib)], default_role="test-role").save(
        project / PROJECT_CONFIG
    )

    rc = main(
        ["pre-commit", "--project-dir", str(project), "--githooks-dir", str(project / ".githooks")]
    )

    assert rc == 0
    assert marker.read_text().strip() == "pre-commit"


@pytest.mark.integration
def test_a_foreign_sessions_role_never_composes_this_projects_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Committing in project A from a shell owned by a session of project B.

    The role must come from A's config, not from B's envelope: the identity is
    read before the foreign pin is dropped, so without the early drop the gates
    of A compose under B's role — HATS-1525's leak on the identity axis
    (HATS-1613 review).
    """
    from ai_hats.cli.githooks_hook import main
    from ai_hats.models import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG
    from ai_hats.session_identity import SessionIdentity

    project = tmp_path / "project"
    (project / ".githooks").mkdir(parents=True)
    lib = tmp_path / "lib"
    _skill(lib, "hook_skill", event="pre-commit", scripts=["git_hooks/check.sh"])
    marker = project / "ran.txt"
    (lib / "skills" / "hook_skill" / "git_hooks" / "check.sh").write_text(
        f'#!/usr/bin/env bash\necho "$AI_HATS_HOOK_EVENT" > "{marker}"\nexit 0\n'
    )
    (lib / "skills" / "hook_skill" / "git_hooks" / "check.sh").chmod(0o755)
    (lib / "traits" / "trait-base").mkdir(parents=True)
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - hook_skill\ninjection: Base.\n"
    )
    (lib / "roles" / "test-role").mkdir(parents=True)
    (lib / "roles" / "test-role" / "config.yaml").write_text(
        "name: test-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: Role.\n"
    )
    ProjectConfig(provider="agy", library_paths=[str(lib)], default_role="test-role").save(
        project / PROJECT_CONFIG
    )

    theirs = tmp_path / "theirs"
    their_session = SessionIdentity(
        id="sid-theirs",
        role="a-role-this-project-never-declared",
        provider="claude",
        project_dir=theirs,
        session_dir=theirs / "s",
    )
    for key, value in their_session.to_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(theirs))

    rc = main(
        ["pre-commit", "--project-dir", str(project), "--githooks-dir", str(project / ".githooks")]
    )

    assert rc == 0
    assert marker.is_file(), (
        "this project's own gate did not run — the foreign session's role composed instead"
    )


@pytest.mark.integration
def test_the_entry_point_leaves_the_process_environment_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping the foreign pin must not reach ``os.environ`` (HATS-1613 review).

    ``main`` is the hook binary AND a unit under test. Dropping in place worked
    for the binary and silently rewrote the process env for every later caller —
    with a randomised suite order, a flake generator rather than a failure.
    """
    from ai_hats.cli.githooks_hook import main

    project = tmp_path / "project"
    (project / ".githooks").mkdir(parents=True)
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / "elsewhere" / ".agent" / "ai-hats"))
    monkeypatch.setenv("AI_HATS_VENV", str(tmp_path / "elsewhere" / ".venv"))
    before = dict(os.environ)

    main(
        ["pre-commit", "--project-dir", str(project), "--githooks-dir", str(project / ".githooks")]
    )

    assert dict(os.environ) == before, "the hook entry point rewrote the process environment"


@pytest.mark.integration
def test_a_project_with_no_role_runs_no_gates_and_passes(tmp_path: Path) -> None:
    from ai_hats.cli.githooks_hook import main
    from ai_hats.models import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    project = tmp_path / "project"
    (project / ".githooks").mkdir(parents=True)
    ProjectConfig(provider="agy").save(project / PROJECT_CONFIG)

    assert (
        main(
            [
                "pre-commit",
                "--project-dir",
                str(project),
                "--githooks-dir",
                str(project / ".githooks"),
            ]
        )
        == 0
    )


# ----- the argv the stub actually builds (HATS-1519) -----


def _gate_project(tmp_path: Path, *, body: str = "exit 0") -> Path:
    """A project composing exactly one pre-commit gate, whose script runs ``body``.

    A real repo on purpose: the journal writer resolves ``--git-common-dir`` from
    the process cwd, never from ``--project-dir``, so a fail-open recorded while
    an uninitialised sandbox is in play lands in whatever checkout the suite is
    running in — 228 synthetic rows in the maintainer's own audit journal before
    HATS-1686 caught it.
    """
    from ai_hats.models import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    project = tmp_path / "project"
    (project / ".githooks").mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)  # noqa: S603, S607
    lib = tmp_path / "lib"
    _skill(lib, "hook_skill", event="pre-commit", scripts=["git_hooks/check.sh"])
    gate = lib / "skills" / "hook_skill" / "git_hooks" / "check.sh"
    gate.write_text(f"#!/usr/bin/env bash\n{body}\n")
    gate.chmod(0o755)

    (lib / "traits" / "trait-base").mkdir(parents=True)
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - hook_skill\ninjection: Base.\n"
    )
    (lib / "roles" / "test-role").mkdir(parents=True)
    (lib / "roles" / "test-role" / "config.yaml").write_text(
        "name: test-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: Role.\n"
    )
    ProjectConfig(provider="agy", library_paths=[str(lib)], default_role="test-role").save(
        project / PROJECT_CONFIG
    )
    return project


def _captured_argv(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record what `main` forwards as the chain's argv, running no gate.

    Deliberately subprocess-free so these carry NO `integration` marker: the
    defect is version-dependent, and `ci-local.sh unit` — the only stage running
    3.11/3.12 — deselects `-m integration`. Marked, they would exercise the bug
    on no CI leg at all (HATS-1519 rework).
    """
    seen: list[list[str]] = []

    def _spy(*, argv: list[str], **_: object) -> int:
        seen.append(list(argv))
        return 0

    monkeypatch.setattr("ai_hats.githooks_run.run_chain", _spy)
    return seen


def test_the_entry_point_accepts_the_argv_the_stub_actually_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stub separates its own flags from the hook's with `--`.

    That separator is pinned on the stub side by
    `test_githooks_stub.py::test_it_delegates_the_event_project_and_hook_args`,
    but nothing fed it back into this entry point — so argparse dropping it only
    on 3.13+ went unnoticed. On 3.11/3.12 it lands as `unrecognized arguments:
    --`, and the resulting SystemExit(2) becomes the hook's verdict: every commit
    in the project refused (HATS-1519).
    """
    from ai_hats.cli.githooks_hook import main

    project = _gate_project(tmp_path)
    seen = _captured_argv(monkeypatch)

    rc = main(
        [
            "pre-commit",
            "--project-dir",
            str(project),
            "--githooks-dir",
            str(project / ".githooks"),
            "--",
            ".git/COMMIT_EDITMSG",
        ]
    )

    assert rc == 0
    assert seen == [[".git/COMMIT_EDITMSG"]]


def test_a_hook_argument_starting_with_a_dash_is_not_read_as_our_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard the separator exists for, held on every supported version.

    Dropping the `--` token and handing the rest to argparse would pass the test
    above and silently lose this: `-x` becomes an unrecognized flag on 3.11/3.12,
    where argparse never consumed the separator in the first place.
    """
    from ai_hats.cli.githooks_hook import main

    project = _gate_project(tmp_path)
    seen = _captured_argv(monkeypatch)

    rc = main(
        [
            "pre-commit",
            "--project-dir",
            str(project),
            "--githooks-dir",
            str(project / ".githooks"),
            "--",
            "-x",
            "--project-dir",
        ]
    )

    assert rc == 0
    assert seen == [["-x", "--project-dir"]]


def test_arguments_this_dispatcher_cannot_parse_refuse_the_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stub/package skew refuses, and states the flag that opens it.

    HATS-1519 skipped here, reasoning the entry point inherits the stub's
    fail-open duty. HATS-1828 retires that: the stub fails open because its
    bytes cannot be fixed at commit time, and this can — `self update` repairs
    it. No project is parsed, so this lone refusal cannot journal itself.
    """
    from ai_hats.cli.githooks_hook import main

    project = _gate_project(tmp_path)
    seen = _captured_argv(monkeypatch)

    rc = main(
        [
            "pre-commit",
            "--project-dir",
            str(project),
            "--githooks-dir",
            str(project / ".githooks"),
            "--hook-stdin-mode",
            "replay",
            "--",
            ".git/COMMIT_EDITMSG",
        ]
    )

    assert rc != 0, "a dispatcher that cannot parse itself must refuse, never skip"
    assert "AI_HATS_GIT_GATE_BROKEN_ACK" in capsys.readouterr().err, "a deny names its hatch"
    assert seen == [], "no gate may run when the dispatcher gave up"


@pytest.mark.integration
def test_a_refusing_gate_still_blocks_the_event(tmp_path: Path) -> None:
    """The other half of the fail-open contract: degrade on OUR failure only.

    A gate's verdict arrives as `run_chain`'s exit code, so widening the skew
    hatch past the parse would turn every refusal into a silent pass — gates
    installed, gates ignored. Runs the gate for real: the verdict this pins is
    a process exit code, and a stubbed chain could not produce one.
    """
    from ai_hats.cli.githooks_hook import main

    ran = tmp_path / "ran.txt"
    project = _gate_project(tmp_path, body=f'printf "%s\\0" "$@" > "{ran}"\nexit 1')

    rc = main(
        [
            "pre-commit",
            "--project-dir",
            str(project),
            "--githooks-dir",
            str(project / ".githooks"),
            "--",
            ".git/COMMIT_EDITMSG",
        ]
    )

    assert rc != 0, "a gate that refused must still block the commit"
    assert ran.read_text().split("\0")[:-1] == [".git/COMMIT_EDITMSG"], (
        "the gate must have run, with git's argument intact across the separator"
    )


# ----- HATS-1597: the dispatcher degrades, it never raises at a human ----------


@pytest.mark.integration
def test_a_gate_that_cannot_be_exec_d_is_refused_at_resolve_time(tmp_path: Path) -> None:
    """Both unrunnable shapes join the existing refusal cascade rather than
    reaching execve. The neighbours on this chain have always checked
    (`githooks_run` for drop-ins and the chained hook, `check_points` for
    `checks:`); only our own gates did not."""
    from ai_hats.githooks_resolve import resolve_git_gates

    skill = _skill(tmp_path / "lib", "s", event="pre-commit", scripts=["git_hooks/g.sh"])
    gate = skill.source_path / "git_hooks" / "g.sh"

    gate.write_text("#!/usr/bin/env bash\nexit 0\n")
    gate.chmod(0o644)
    refusals = resolve_git_gates(_result(skill), "pre-commit").refusals
    assert len(refusals) == 1 and "not executable" in refusals[0], refusals

    gate.write_text("exit 0\n")
    gate.chmod(0o755)
    refusals = resolve_git_gates(_result(skill), "pre-commit").refusals
    assert len(refusals) == 1 and "shebang" in refusals[0], refusals


@pytest.mark.integration
def test_a_composition_that_refuses_blocks_the_commit_and_names_its_hatch(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """A CheckBindingError (a removed script, an unknown point name) renders
    friendly only in the click layer, which a git hook never enters — so it
    reached the human as a traceback and exit 1. Never a traceback (HATS-1597),
    and since HATS-1828 never a pass either: no gate ran, which is ai-hats' own
    failure rather than anybody's verdict.

    Patched on `materialize`: `main` imports the name inside its own body, so
    the source module is the only place a stub is observable.
    """
    from ai_hats.cli.githooks_hook import main

    project = _gate_project(tmp_path)

    def _boom(*_a, **_kw):
        raise RuntimeError("composition refused: unknown point 'edge:typo'")

    monkeypatch.setattr("ai_hats.materialize.compose_for_role", _boom)

    rc = main(
        ["pre-commit", "--project-dir", str(project), "--githooks-dir", str(project / ".githooks")]
    )

    err = capsys.readouterr().err
    assert rc != 0, "a composition ai-hats cannot build is its failure, not a pass"
    assert "edge:typo" in err, err
    assert "AI_HATS_GIT_GATE_BROKEN_ACK" in err, "a deny must name its hatch"


@pytest.mark.integration
def test_the_hatch_turns_that_refusal_back_into_a_recorded_skip(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """The other half: the flag the refusal names has to actually work.

    And taking it stays loud — ADR-0020 D2 forbids passing a gate SILENTLY, so
    the skip is on record, in the sandbox this test owns (HATS-1686).

    Breaks delivery for real — a declared gate whose file is gone — rather than
    stubbing `compose_for_role`: this is the refusal a project actually hits when
    a skill stops shipping a script, and the isolation ratchet counts every stub.
    """
    from ai_hats.cli.githooks_hook import main

    project = _gate_project(tmp_path)
    (tmp_path / "lib" / "skills" / "hook_skill" / "git_hooks" / "check.sh").unlink()
    monkeypatch.setenv("AI_HATS_GIT_GATE_BROKEN_ACK", "1")

    rc = main(
        ["pre-commit", "--project-dir", str(project), "--githooks-dir", str(project / ".githooks")]
    )

    err = capsys.readouterr().err
    assert rc == 0, f"the named hatch must open: {err}"
    journal = project / ".git" / "ai-hats" / "bypasses.jsonl"
    assert journal.is_file(), f"the skip was not journalled: {err}"
    assert "does not exist" in journal.read_text(encoding="utf-8")


# ----- HATS-1643: one unreadable identity, two reactions ----------------------


@pytest.mark.integration
def test_a_session_too_old_to_name_itself_degrades_to_the_configured_role(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """Nothing is torn here — the envelope was never written — so the configured
    role is a sound answer and the gates still RUN, degraded and on record.

    The inversion this closes: a session that cannot name itself is more
    suspicious than no session at all, yet it used to get the weaker check.
    """
    from ai_hats.cli.githooks_hook import main

    marker = tmp_path / "ran.txt"
    project = _gate_project(tmp_path, body=f'touch "{marker}"')
    monkeypatch.setenv("AI_HATS_SESSION_ID", "20260101-000000-1-1")
    monkeypatch.delenv("AI_HATS_SESSION_IDENTITY", raising=False)

    rc = main(
        ["pre-commit", "--project-dir", str(project), "--githooks-dir", str(project / ".githooks")]
    )

    err = capsys.readouterr().err
    assert rc == 0, err
    assert marker.exists(), f"the degraded path must still run the gates:\n{err}"
    journal = project / ".git" / "ai-hats" / "bypasses.jsonl"
    assert journal.is_file(), f"the degrade was not journalled:\n{err}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "envelope",
    ["}not json{", '"a string, not an object"', '{"v": 99, "id": "x"}'],
    ids=["torn", "not-an-object", "version-drift"],
)
def test_an_untrustworthy_envelope_refuses_instead_of_skipping(
    tmp_path: Path, capsys, monkeypatch, envelope: str
) -> None:
    """The type's own docstring says 'never a skip', and its consumer did exactly
    that: `return 0`, on the same condition rack answers with an abort.

    Corruption and version drift are trust failures, not staleness — nothing here
    licenses guessing a role, so the event stops and names its hatch.
    """
    from ai_hats.cli.githooks_hook import main

    marker = tmp_path / "ran.txt"
    project = _gate_project(tmp_path, body=f'touch "{marker}"')
    monkeypatch.setenv("AI_HATS_SESSION_ID", "20260101-000000-1-1")
    monkeypatch.setenv("AI_HATS_SESSION_IDENTITY", envelope)

    rc = main(
        ["pre-commit", "--project-dir", str(project), "--githooks-dir", str(project / ".githooks")]
    )

    err = capsys.readouterr().err
    assert rc != 0, f"an untrusted envelope must not wave the commit through:\n{err}"
    assert not marker.exists(), "no gate may run under an identity we do not trust"
    assert "AI_HATS_GIT_GATE_BROKEN_ACK" in err, "a deny must name its hatch"


@pytest.mark.integration
def test_a_missing_journal_warns_that_the_hatch_will_not_be_recorded(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """The gates still run — no journal is not a disarm — but the warning has to
    say the hatch is now unrecordable.

    Otherwise the ADR-0020 D2 promise ("never SILENTLY") is quietly void exactly
    when someone reaches for the flag, and nothing would ever say so.
    """
    from ai_hats.cli.githooks_hook import main

    marker = tmp_path / "ran.txt"
    project = _gate_project(tmp_path, body=f'touch "{marker}"')
    # A library that is structurally complete but ships no `hooks/` — the shape a
    # partial install presents, and the only one `builtin_library_hooks` answers
    # None for. A merely-empty dir is rejected as a library and falls back.
    hookless = tmp_path / "hookless-lib"
    (hookless / "core" / "pipelines").mkdir(parents=True)
    (hookless / "usage").mkdir()
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(hookless))

    rc = main(
        ["pre-commit", "--project-dir", str(project), "--githooks-dir", str(project / ".githooks")]
    )

    err = capsys.readouterr().err
    assert rc == 0, err
    assert marker.exists(), f"a missing journal must not disarm the gates:\n{err}"
    assert "AI_HATS_GIT_GATE_BROKEN_ACK" in err and "NOT be recorded" in err, err
