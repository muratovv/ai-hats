"""e2e: a declarative ``checks:`` binding gates a real rack FSM edge (HATS-1141).

The REAL ``rack`` console script from the shared launcher venv, in a git sandbox
whose project-local library binds one script to ``edge:brainstorm--plan`` — the
cheapest real edge (no worktree, no merge consent).

Fail-under-revert: restore the empty ``consumer_subscribers``
(``rack_consumers.py:30``) and every refusal below becomes a clean transition.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from _helpers.git import git, init_repo

pytestmark = pytest.mark.integration

TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
EDGE = "edge:brainstorm--plan"
#: The event half of a check-log filename; the binding half follows it
#: (``<event>~<skill>~<script>.log``, ``rack_consumers._log_path``).
EDGE_LOG_PREFIX = "edge-brainstorm--plan"
SKILL = "gate-skill"

#: Where each known surface mirrors the session's composed skills — since
#: HATS-1540 also the root a bound check resolves from (ADR-0019 D9 / R3.1), so
#: these are three answers to one question and the ACTIVE surface picks. Literal
#: because the decoys need the roots the active surface does NOT own; every entry
#: is pinned against ``Provider.session_skills_root`` below, so it cannot drift.
#  comment-length: allow — why a literal table is safe here is the contract
SURFACE_SKILL_TREES = {
    "claude": Path("plugin") / "skills",
    "agy": Path("rules") / ".agents" / "skills",
    "cline": Path("skills"),
}

SKILL_MD = """\
---
name: gate-skill
description: A skill shipping the gate scripts these tests bind.
---

# Gate Skill
"""

#: script name -> body. One skill ships them all; a role picks one.
SCRIPTS = {
    "refuse.sh": 'printf "drain the review notes first\\n"\nexit 2\n',
    "pass.sh": 'printf "gate cleared\\n"\nexit 0\n',
    "broke.sh": 'printf "ruff exploded\\n"\nexit 1\n',
    # Deliberate escapes, not env-driven colour: run_hook already scrubs
    # FORCE_COLOR out of the child env, so only a script that writes them
    # itself can prove the reason channel is cleaned (hook_exec._decode_tail).
    "ansi.sh": 'printf "\\033[31mdrain\\033[0m the \\033[1mreview\\033[0m notes\\n"\nexit 2\n',
}

#: role name -> (script, on_error). ``plain`` binds nothing at all.
ROLES = {
    "refusing": ("refuse.sh", "refuse"),
    "passing": ("pass.sh", "refuse"),
    "warned": ("broke.sh", "warn"),
    "ansi": ("ansi.sh", "refuse"),
}

_ROLE_YAML = """\
name: {name}
priorities:
  - Reliability
composition:
  traits: []
  rules: []
  skills:
    - {skill}
  checks:
    - skill: {skill}
      script: {script}
      "on": [{edge}]
      on_error: {on_error}
injection: |
  # ROLE: {name}
"""

_PLAIN_YAML = f"""\
name: plain
priorities:
  - Reliability
composition:
  traits: []
  rules: []
  skills:
    - {SKILL}
injection: |
  # ROLE: PLAIN
"""

_PROJECT_YAML = """\
schema_version: 4
provider: {provider}
default_role: {role}
task_prefix: SBX
ai_hats_dir: .agent/ai-hats
"""


# ---------------------------------------------------------------------------
# sandbox
# ---------------------------------------------------------------------------


def _write_script(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _seed_library(project: Path) -> None:
    """One skill shipping every script, one role per outcome class."""
    lib = project / "libraries"
    skill_dir = lib / "skills" / SKILL
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    for name, body in SCRIPTS.items():
        _write_script(skill_dir / name, body)

    for name, (script, on_error) in ROLES.items():
        role_dir = lib / "roles" / name
        role_dir.mkdir(parents=True)
        (role_dir / "config.yaml").write_text(
            _ROLE_YAML.format(name=name, skill=SKILL, script=script, edge=EDGE, on_error=on_error),
            encoding="utf-8",
        )
    plain = lib / "roles" / "plain"
    plain.mkdir(parents=True)
    (plain / "config.yaml").write_text(_PLAIN_YAML, encoding="utf-8")


@pytest.fixture
def gate_project(shared_launcher, tmp_path: Path):
    """Factory: ``(project, env)`` for a git sandbox whose active role is ``role``.

    ``AI_HATS_USER_HOME`` is re-pinned per test (the shared launcher's is
    session-scoped) so the session cache — and the skill mirror in it a bound
    check resolves from — is born and swept with this test's ``tmp_path``.
    """
    _launcher, base_env, _venv = shared_launcher
    counter = {"n": 0}

    def make(role: str, *, provider: str = "claude") -> tuple[Path, dict[str, str]]:
        counter["n"] += 1
        project = tmp_path / f"proj{counter['n']}"
        project.mkdir()
        # A MAIN checkout, not a linked worktree: ``.git`` must be a directory,
        # or the resolver's D9 clause 4 guard refuses before any check runs.
        init_repo(project, branch="master", harden=True)
        (project / "ai-hats.yaml").write_text(
            _PROJECT_YAML.format(provider=provider, role=role), encoding="utf-8"
        )
        (project / ".gitignore").write_text(".agent/\n", encoding="utf-8")
        (project / TASKS_SUB).mkdir(parents=True)
        _seed_library(project)
        git(project, "add", "-A")
        git(project, "commit", "-m", "seed")

        home = tmp_path / f"home{counter['n']}"
        home.mkdir()
        env = {
            **base_env,
            "AI_HATS_USER_HOME": str(home),
            # Both roots per test: the launcher's are session-scoped, and the
            # session mirror this file plants and reads must not outlive it.
            "AI_HATS_CACHE_HOME": str(tmp_path / f"cache{counter['n']}"),
            "AI_HATS_SESSION_ID": f"e2e-checks-{counter['n']}",
        }
        return project, env

    return make


# ---------------------------------------------------------------------------
# driving the real binary
# ---------------------------------------------------------------------------


def _rack(
    rack: Path, *args: str, cwd: Path, env: dict[str, str], unset: str = ""
) -> subprocess.CompletedProcess[str]:
    """One real ``rack`` invocation; ``unset`` drops a var via real ``env -u``."""
    argv = ["env", "-u", unset] if unset else []
    return subprocess.run(  # noqa: S603 - binary from the shared-launcher fixture
        [*argv, str(rack), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _create(rack: Path, project: Path, env: dict[str, str], title: str = "gate probe") -> str:
    created = _rack(rack, "create", title, cwd=project, env=env)
    assert created.returncode == 0, created.stderr
    match = re.search(r"Created: (\S+)", created.stdout)
    assert match, created.stdout
    return match.group(1)


def _card(project: Path, task_id: str) -> Path:
    return project / TASKS_SUB / task_id / "task.yaml"


def _checks_dir(project: Path, task_id: str) -> Path:
    return project / TASKS_SUB / task_id / ".checks"


def _check_log(project: Path, task_id: str, script: str) -> Path:
    """One log per (task, edge, binding), so two bindings on one edge cannot
    truncate each other's transcript (HATS-1137)."""
    return _checks_dir(project, task_id) / f"{EDGE_LOG_PREFIX}~{SKILL}~{script}.log"


def _ran_script(log: Path) -> Path:
    """The script ``run_hook`` actually spawned, from the log's own header."""
    header = log.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    match = re.search(r"script=(.+) timeout=", header)
    assert match, header
    return Path(match.group(1))


def _reason(result: subprocess.CompletedProcess[str]) -> str:
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "aborted", payload
    assert payload["error"]["subscriber"] == "checks", payload
    return payload["error"]["reason"]


def _outcomes(result: subprocess.CompletedProcess[str]) -> dict[str, dict]:
    payload = json.loads(result.stdout)
    return {o["subscriber"]: o for o in payload["journal"][0]["outcomes"]}


def _session_dir(project: Path, env: dict[str, str], session_id: str) -> Path:
    """The subprocess's own session cache dir, via the production path function
    under the subprocess's exact env — re-deriving it here is how a test starts
    watching a root the child never writes to."""
    from ai_hats.paths import session_cache_dir

    with mock.patch.dict(os.environ, env, clear=True):
        return session_cache_dir(project, session_id)


def _sessions_root(project: Path, env: dict[str, str]) -> Path:
    from ai_hats.paths import session_cache_root

    with mock.patch.dict(os.environ, env, clear=True):
        return session_cache_root(project)


def _mirror_root(project: Path, env: dict[str, str], session_id: str = "") -> Path:
    """Where the project's ACTIVE surface mirrors this session's composed skills.

    Asked of the real accessor (``Provider.session_skills_root``, HATS-1540) with
    the surface read back out of the sandbox's own ``ai-hats.yaml`` — the two
    steps ``composition_seam.session_skills_root_for_checks`` takes. Hard-coding
    a root here would let a test plant where the child never reads and still go
    green on some other surface's tree.
    """
    from ai_hats.models import ProjectConfig
    from ai_hats.paths.constants import PROJECT_CONFIG
    from ai_hats.providers import get_provider

    with mock.patch.dict(os.environ, env, clear=True):
        surface = ProjectConfig.from_yaml(project / PROJECT_CONFIG).provider
        return get_provider(surface).session_skills_root(
            project, session_id or env["AI_HATS_SESSION_ID"]
        )


def _seed_mirror(project: Path, env: dict[str, str], *, script: str, body: str) -> Path:
    """Plant the bound skill into the surface's mirror, the way launch would.

    HATS-1540 retired the channel's private ``<sid>/checks/`` copy: the surface's
    own skill mirror is the only one, so this is where a session-mode binding
    resolves. The bytes deliberately differ from the library's — the refusal
    quotes them, so the message itself names which root the resolver chose.
    """
    dest = _mirror_root(project, env) / SKILL
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    _write_script(dest / script, body)
    return dest / script


@pytest.fixture
def rack_bin(shared_launcher) -> Path:
    _launcher, _env, venv = shared_launcher
    rack = venv / "bin" / "rack"
    assert rack.is_file(), "ai-hats-rack must install the `rack` console script"
    return rack


@pytest.fixture(scope="session")
def venv_surfaces(shared_launcher) -> dict[str, bool]:
    """``{provider: roots_a_bound_check}`` as the venv under test sees it.

    The launcher venv resolves the out-of-tree surfaces from the index, so which
    ones it has — and which of them predate a given accessor — is a property of
    the install, not of this branch.

    HATS-1540 made the ANSWER here two-part: a surface must both reach the
    artifact builder AND say where it mirrors skills (`session_skills_root`,
    concrete on `Provider` with a `None` default, so an older surface package
    keeps importing and simply cannot root a bound check). A surface failing
    either half cannot exercise these cases and is skipped with that named as
    the reason — a coordinated release of core + surface is what clears it, and
    `check_snapshot.surface_skew_notice` is what tells a real operator.
    """
    _launcher, _env, venv = shared_launcher
    probe = subprocess.run(  # noqa: S603 - interpreter from the shared fixture
        [
            str(venv / "bin" / "python"),
            "-c",
            "import json, pathlib; "
            "from ai_hats.providers import provider_names, get_provider; "
            "p = pathlib.Path('/tmp'); "
            "print(json.dumps({n: bool(get_provider(n).handles_artifact_categories() "
            "and get_provider(n).session_skills_root(p, 'probe') is not None) "
            "for n in provider_names()}))",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert probe.returncode == 0, probe.stderr
    return json.loads(probe.stdout)


# ---------------------------------------------------------------------------
# 1. the tracer bullet — R1 / R2 / R3.3 / R5 (in-session branch)
# ---------------------------------------------------------------------------

MIRROR_WORDS = "the mirror says: drain the review notes first"


def test_a_bound_check_refuses_a_real_transition_and_leaves_the_card_untouched(
    gate_project, rack_bin
):
    """R1 + R2 + R3.3: the declared binding fires in the lock, the edge is
    refused before the single persist, and the reason is the child's own words.

    The mirror's copy of the script says something the library's does not — so
    this also pins D9 clause 2 as HATS-1540 re-cut it: in a session the bytes
    come from the surface's skill mirror, never from the live library.
    """
    project, env = gate_project("refusing")
    task_id = _create(rack_bin, project, env)
    _seed_mirror(project, env, script="refuse.sh", body=f'printf "{MIRROR_WORDS}\\n"\nexit 2\n')
    before = _card(project, task_id).read_bytes()

    refused = _rack(rack_bin, "transition", task_id, "plan", cwd=project, env=env)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    # R2: nothing was persisted — not the state, not the `updated` stamp.
    assert _card(project, task_id).read_bytes() == before
    # The human channel is stderr (`cli_common.fail`), prefixed by the dispatcher.
    assert f"{EDGE} aborted by 'checks'" in refused.stderr
    assert MIRROR_WORDS in refused.stderr
    assert "Traceback" not in refused.stderr, "a refusal must be typed, not a stack"

    as_json = _rack(rack_bin, "transition", task_id, "plan", "--json", cwd=project, env=env)
    assert as_json.returncode == 1
    # R3.3: verbatim in --json — the prefix belongs to the text surface only.
    assert _reason(as_json) == MIRROR_WORDS
    assert _card(project, task_id).read_bytes() == before


# ---------------------------------------------------------------------------
# 2. out of session — R3.2 / D9 clause 3
# ---------------------------------------------------------------------------


def test_out_of_session_the_same_binding_refuses_from_the_live_library(gate_project, rack_bin):
    """R3.2: no ``AI_HATS_SESSION_ID`` (bare terminal, cron, a git hook) resolves
    live, never 'absent'. Stripped by real ``env -u``, from an env that HAS it —
    a resolver keyed on the session tree goes quiet exactly here."""
    project, env = gate_project("refusing")
    task_id = _create(rack_bin, project, env)
    before = _card(project, task_id).read_bytes()

    refused = _rack(
        rack_bin,
        "transition",
        task_id,
        "plan",
        "--json",
        cwd=project,
        env=env,
        unset="AI_HATS_SESSION_ID",
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert _reason(refused) == "drain the review notes first"
    assert _card(project, task_id).read_bytes() == before
    # The live library copy ran — no session root was involved.
    ran = _ran_script(_check_log(project, task_id, "refuse.sh"))
    assert ran == project / "libraries" / "skills" / SKILL / "refuse.sh"


# ---------------------------------------------------------------------------
# 3. surface independence — R3.1 / R6
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("surface", sorted(SURFACE_SKILL_TREES))
def test_a_bound_check_runs_the_active_surfaces_mirror_and_no_other(
    gate_project, rack_bin, venv_surfaces, surface
):
    """R3.1 without auth — surface independence as HATS-1540 re-cut it.

    The channel's own ``<sid>/checks/`` root is retired, so "the gate ignores
    every surface tree" is no longer true of anything: the surface mirror IS the
    resolution root. What survives is the other half of the same idea, and it is
    the half that can still go wrong — the resolver must read the root the
    ACTIVE surface declares, and neither of the other two.

    Shaped as a choice rather than a lookup: the active surface's mirror holds
    the REFUSING script and both other surfaces' trees hold PASSING decoys of
    the same skill and script name. A resolver keyed on a fixed surface, or on
    "whichever tree happens to exist", takes the edge instead of refusing.
    """  # comment-length: allow — this test's premise was inverted; say why
    _require_a_rootable_surface(venv_surfaces, surface)
    project, env = gate_project("refusing", provider=surface)
    task_id = _create(rack_bin, project, env)

    session_dir = _session_dir(project, env, env["AI_HATS_SESSION_ID"])
    # The one place the literal table is pinned against the real accessor: the
    # decoys below are planted from it, and a stale entry would spell a "decoy"
    # onto the very root the resolver reads — making the refusal prove nothing.
    assert _mirror_root(project, env) == session_dir / SURFACE_SKILL_TREES[surface]

    mirrored = _seed_mirror(
        project, env, script="refuse.sh", body=f'printf "{MIRROR_WORDS}\\n"\nexit 2\n'
    )
    others = {name: tree for name, tree in SURFACE_SKILL_TREES.items() if name != surface}
    for tree in others.values():
        decoy_dir = session_dir / tree / SKILL
        decoy_dir.mkdir(parents=True)
        _write_script(decoy_dir / "refuse.sh", 'printf "decoy passed\\n"\nexit 0\n')

    refused = _rack(rack_bin, "transition", task_id, "plan", "--json", cwd=project, env=env)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert _reason(refused) == MIRROR_WORDS
    ran = _ran_script(_check_log(project, task_id, "refuse.sh"))
    assert ran == mirrored
    for name, tree in others.items():
        assert not ran.is_relative_to(session_dir / tree), f"resolved through the {name} tree"


def _live_session_gate(
    launcher: Path, rack: Path, project: Path, env: dict[str, str], surface: str
) -> None:
    """Drive a REAL surface session and gate a transition from inside it.

    The session cache — the skill mirror with it — is dropped when the session
    ends (``runtime_common._cleanup_session_cache``), so the provider child is
    held alive while the gate runs, which is also the production shape: an agent
    inside a live session invoking ``rack``.

    The sid is minted by the child, so the mirror is FOUND rather than computed:
    the glob is this surface's own declared tree under a wildcard sid, and once
    the sid is known the accessor is asked to confirm the tree — that is what
    keeps ``SURFACE_SKILL_TREES`` honest against a real surface's writer.
    """  # comment-length: allow — why the root is discovered, not computed
    tree = SURFACE_SKILL_TREES[surface]
    task_id = _create(rack, project, env)
    live_env = {k: v for k, v in env.items() if k != "AI_HATS_SESSION_ID"}
    sessions = _sessions_root(project, env)

    child = subprocess.Popen(  # noqa: S603 - launcher from the shared fixture
        [str(launcher), "-r", "refusing"],
        cwd=str(project),
        env=live_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 90
        planted: list[Path] = []
        while time.monotonic() < deadline and not planted:
            planted = list(sessions.glob(f"*/{tree.as_posix()}/{SKILL}/refuse.sh"))
            if not planted and child.poll() is not None:
                said, _ = child.communicate()
                raise AssertionError(
                    f"the surface exited (rc={child.returncode}) before it mirrored the "
                    f"bound skill; no session under {sessions}\n{(said or '')[-1500:]}"
                )
            time.sleep(0.1)
        assert planted, f"no skill mirror appeared under {sessions}/*/{tree.as_posix()} within 90s"

        mirrored = planted[0]
        session_dir = mirrored.parents[len(tree.parts) + 1]
        # The writer wrote where the reader's accessor says it would: this is the
        # live half of the drift guard the in-process cases assert on the table.
        assert _mirror_root(project, env, session_dir.name) == session_dir / tree

        refused = _rack(
            rack,
            "transition",
            task_id,
            "plan",
            "--json",
            cwd=project,
            env={**live_env, "AI_HATS_SESSION_ID": session_dir.name},
        )
        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert _reason(refused) == "drain the review notes first"
        # Positive control for the mode split: the bytes came from the session's
        # mirror, not from the live library the surface copied them out of.
        ran = _ran_script(_check_log(project, task_id, "refuse.sh"))
        assert ran == mirrored
        assert not ran.is_relative_to(project), "in a session the library copy must not run"
    finally:
        child.terminate()
        try:
            child.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()


def _require_a_rootable_surface(venv_surfaces: dict[str, bool], surface: str) -> None:
    """Two ways a surface has no session root to gate from, both ABSENT premises
    rather than broken ones — and each says so at launch, to a real operator.

    Below ADR-0018 it mirrors no skills BY DESIGN (`legacy_launch_notices` names
    the loss). Below HATS-1540 it mirrors them and will not say where
    (`surface_skew_notice`), which a coordinated release of core + surface
    clears. Either way the property under test cannot be exercised here."""
    if surface not in venv_surfaces:
        pytest.skip(f"surface {surface!r} is not installed in the venv under test")
    if not venv_surfaces[surface]:
        pytest.skip(
            f"surface {surface!r} cannot root a bound check in the venv under test — it is "
            f"below the artifact builder, or a package older than `session_skills_root` "
            f"(HATS-1540 ships core and surface together)"
        )


def test_the_gate_fires_inside_a_live_claude_session(
    gate_project, rack_bin, shared_launcher, venv_surfaces, requires_claude_auth
):
    """R3.1 with the real surface: claude mirrors — and so gates — at
    ``<sid>/plugin/skills``."""
    launcher, _env, _venv = shared_launcher
    _require_a_rootable_surface(venv_surfaces, "claude")
    project, env = gate_project("refusing", provider="claude")
    _live_session_gate(launcher, rack_bin, project, env, "claude")


def test_the_gate_fires_inside_a_live_agy_session(
    gate_project, rack_bin, shared_launcher, venv_surfaces, requires_agy_auth
):
    """R3.1 with the real surface: agy mirrors — and so gates — at
    ``<sid>/rules/.agents/skills``."""
    launcher, _env, _venv = shared_launcher
    _require_a_rootable_surface(venv_surfaces, "agy")
    project, env = gate_project("refusing", provider="agy")
    _live_session_gate(launcher, rack_bin, project, env, "agy")


def test_the_gate_fires_inside_a_live_cline_session(
    gate_project, rack_bin, shared_launcher, venv_surfaces, requires_cline_auth
):
    """R3.1 with the real surface: cline mirrors — and so gates — at
    ``<sid>/skills``."""
    launcher, _env, _venv = shared_launcher
    _require_a_rootable_surface(venv_surfaces, "cline")
    project, env = gate_project("refusing", provider="cline")
    _live_session_gate(launcher, rack_bin, project, env, "cline")


# ---------------------------------------------------------------------------
# 4. a passing check does not block; a project that binds nothing pays nothing
# ---------------------------------------------------------------------------


def test_a_passing_check_lets_the_transition_complete(gate_project, rack_bin):
    """``exit 0`` is not a veto. The journal entry is the positive control: the
    subscriber RAN and said ok, rather than being absent from the ladder."""
    project, env = gate_project("passing")
    task_id = _create(rack_bin, project, env)
    _seed_mirror(project, env, script="pass.sh", body='printf "gate cleared\\n"\nexit 0\n')

    taken = _rack(rack_bin, "transition", task_id, "plan", "--json", cwd=project, env=env)

    assert taken.returncode == 0, taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "plan"
    assert _outcomes(taken)["checks"]["outcome"] == "ok"


def test_a_project_that_binds_nothing_takes_the_edge_untaxed(gate_project, rack_bin):
    """The regression guard for every project that never adopts the channel: the
    runner is wired, resolves nothing, writes nothing, and refuses nothing."""
    project, env = gate_project("plain")
    task_id = _create(rack_bin, project, env)

    taken = _rack(rack_bin, "transition", task_id, "plan", "--json", cwd=project, env=env)

    assert taken.returncode == 0, taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "plan"
    assert _outcomes(taken)["checks"]["outcome"] == "ok"
    assert not _checks_dir(project, task_id).exists(), "no bindings must write no logs"


# ---------------------------------------------------------------------------
# 5. on_error: warn — R4
# ---------------------------------------------------------------------------


def test_on_error_warn_downgrades_a_broken_check_but_records_it(gate_project, rack_bin):
    """R4: ``BROKE`` (exit 1) is the one downgradable class — the edge is taken,
    and the card carries why, so a softened gate is never a silent one."""
    project, env = gate_project("warned")
    task_id = _create(rack_bin, project, env)
    _seed_mirror(project, env, script="broke.sh", body='printf "ruff exploded\\n"\nexit 1\n')

    taken = _rack(rack_bin, "transition", task_id, "plan", "--json", cwd=project, env=env)

    assert taken.returncode == 0, taken.stderr
    assert json.loads(taken.stdout)["task"]["state"] == "plan"
    trace = "\n".join(entry["message"] for entry in json.loads(taken.stdout)["task"]["work_log"])
    assert "downgraded by on_error: warn" in trace
    assert f"{SKILL}/broke.sh on {EDGE}" in trace
    assert "ruff exploded" in trace
    assert "hook broke: exited 1" in trace


# ---------------------------------------------------------------------------
# 6. no ANSI in the reason — R3.3
# ---------------------------------------------------------------------------


def test_the_reason_carries_no_escape_sequences_under_force_color(gate_project, rack_bin):
    """The reason is the agent's input, so it must be plain text even when the
    check writes escapes on purpose. Asserted as ABSENCE — running the output
    through a strip helper first would only test the helper. The log file keeps
    the escapes, which is what makes the absence non-vacuous."""
    project, env = gate_project("ansi")
    task_id = _create(rack_bin, project, env)
    _seed_mirror(project, env, script="ansi.sh", body=SCRIPTS["ansi.sh"])

    refused = _rack(
        rack_bin,
        "transition",
        task_id,
        "plan",
        "--json",
        cwd=project,
        env={**env, "FORCE_COLOR": "3", "CLICOLOR_FORCE": "1"},
    )

    assert refused.returncode == 1, refused.stdout + refused.stderr
    reason = _reason(refused)
    assert reason == "drain the review notes"
    assert "\x1b" not in reason
    assert "\x1b" not in refused.stdout
    # Positive control: the script really did emit escapes, and the postmortem
    # record keeps them verbatim (hook_exec._decode_tail touches the tail only).
    assert "\x1b[31m" in _check_log(project, task_id, "ansi.sh").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 7. the log lands beside the card and stays out of the doc registry — R3.4
# ---------------------------------------------------------------------------


def test_the_check_log_lands_under_dot_checks_and_is_no_document(gate_project, rack_bin):
    """R3.4: the full output belongs next to the card, and the dot-component
    keeps it out of ``rack context`` — a gate must not pin its own transcript
    into every agent's reading list (``docstore._is_document``)."""
    project, env = gate_project("passing")
    task_id = _create(rack_bin, project, env)
    _seed_mirror(project, env, script="pass.sh", body='printf "gate cleared\\n"\nexit 0\n')

    assert _rack(rack_bin, "transition", task_id, "plan", cwd=project, env=env).returncode == 0

    log = _check_log(project, task_id, "pass.sh")
    assert log.is_file(), f"expected the check log at {log}"
    text = log.read_text(encoding="utf-8")
    assert f"point={EDGE}" in text
    assert "gate cleared" in text

    context = _rack(rack_bin, "context", task_id, cwd=project, env=env)
    assert context.returncode == 0, context.stderr
    # Positive control: the registry DOES list the card's ordinary files.
    assert "plan.md" in context.stdout
    assert ".checks" not in context.stdout
    assert log.name not in context.stdout
