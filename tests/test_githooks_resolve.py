"""Unit tests for the live git-gate resolver (HATS-1337, ADR-0020 D3).

The dispatcher has no ai-hats process of its own; it spawns one and asks this
resolver which gates the composed role declares for an event. What the retired
flatten-copy used to provide as a side effect — containment and chain order —
is asserted here.
"""

from __future__ import annotations

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


def _hook_args_project(tmp_path: Path, seen: Path, *, gate_exit: int = 0) -> Path:
    """A project whose single pre-commit gate records the argv it was handed."""
    from ai_hats.models import ProjectConfig
    from ai_hats.paths import PROJECT_CONFIG

    project = tmp_path / "project"
    (project / ".githooks").mkdir(parents=True)
    lib = tmp_path / "lib"
    _skill(lib, "hook_skill", event="pre-commit", scripts=["git_hooks/check.sh"])
    gate = lib / "skills" / "hook_skill" / "git_hooks" / "check.sh"
    gate.write_text(f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "{seen}"\nexit {gate_exit}\n')
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


@pytest.mark.integration
def test_the_entry_point_accepts_the_argv_the_stub_actually_builds(tmp_path: Path) -> None:
    """The stub separates its own flags from the hook's with `--`.

    That separator is pinned on the stub side by
    `test_githooks_stub.py::test_it_delegates_the_event_project_and_hook_args`,
    but nothing fed it back into this entry point — so argparse dropping it only
    on 3.13+ went unnoticed. On 3.11/3.12 it lands as `unrecognized arguments:
    --`, and the resulting SystemExit(2) becomes the hook's verdict: every commit
    in the project refused (HATS-1519).
    """
    from ai_hats.cli.githooks_hook import main

    seen = tmp_path / "argv.txt"
    project = _hook_args_project(tmp_path, seen)

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
    assert seen.read_text().split("\0")[:-1] == [".git/COMMIT_EDITMSG"]


@pytest.mark.integration
def test_a_hook_argument_starting_with_a_dash_is_not_read_as_our_flag(tmp_path: Path) -> None:
    """The guard the separator exists for, held on every supported version.

    Dropping the `--` token and handing the rest to argparse would pass the test
    above and silently lose this: `-x` becomes an unrecognized flag on 3.11/3.12,
    where argparse never consumed the separator in the first place.
    """
    from ai_hats.cli.githooks_hook import main

    seen = tmp_path / "argv.txt"
    project = _hook_args_project(tmp_path, seen)

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
    assert seen.read_text().split("\0")[:-1] == ["-x", "--project-dir"]


@pytest.mark.integration
def test_arguments_this_dispatcher_cannot_parse_skip_the_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stub newer than the installed ai-hats must not wedge the commit.

    The separator was one shape of that skew; a flag a later stub learns to pass
    is the next. argparse answers both with SystemExit(2) from inside `main`,
    past the stub's import guard, and that 2 becomes the hook's verdict — so the
    degradation has to happen here (HATS-1519).
    """
    from ai_hats.cli.githooks_hook import main

    seen = tmp_path / "argv.txt"
    project = _hook_args_project(tmp_path, seen)

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

    assert rc == 0, "a dispatcher that cannot parse itself must skip, never refuse"
    assert "fail-open" in capsys.readouterr().err
    assert not seen.exists(), "no gate may run when the dispatcher gave up"


@pytest.mark.integration
def test_a_refusing_gate_still_blocks_the_event(tmp_path: Path) -> None:
    """The other half of the fail-open contract: degrade on OUR failure only.

    A gate's verdict arrives as `run_chain`'s exit code, so widening the skew
    hatch past the parse would turn every refusal into a silent pass — gates
    installed, gates ignored.
    """
    from ai_hats.cli.githooks_hook import main

    seen = tmp_path / "argv.txt"
    project = _hook_args_project(tmp_path, seen, gate_exit=1)

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
    assert seen.exists(), "the gate must actually have run"
