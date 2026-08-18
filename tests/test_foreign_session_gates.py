"""A foreign session must not choose this project's gates (HATS-1631).

HATS-1613 closed this on the git-hook road; on wt and rack the identity is read
above the drop, with no scope test at all. The failure is not the loud refusal
the card was filed with (that needs the foreign role to not exist) — a foreign
role that DOES exist and declares nothing resolves to zero gates, so the merge
and the transition sail through in silence.

Every test asserts on the GATE'S EFFECT: the fixture's gate exits non-zero, so a
road that refuses is a road that resolved it. Counting rows would pass on a gate
resolved and never run.
"""  # comment-length: allow — which failure is being pinned is the contract

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ai_hats_core.deadline import Deadline
from ai_hats_wt.manager import LifecycleContext

from ai_hats.session_identity import ENV_SESSION_IDENTITY, IDENTITY_VERSION, SessionIdentity
from ai_hats.wt_lifecycle import WT_PRE_MERGE

FOREIGN_PROJECT = Path("/nonexistent/project-B")

ROLE_CONFIG = """\
name: gate-role
composition:
  skills:
    - gate-skill
  apps:
    wt:
      - run: gate-skill/hooks/gate.sh
        at: [pre-merge]
        on_error: refuse
    rack:
      tasks:
        - run: gate-skill/hooks/gate.sh
          at: [review->done]
          on_error: refuse
"""

PROJECT_CONFIG = """\
schema_version: 4
ai_hats_dir: .agent/ai-hats
provider: claude
active_role: gate-role
"""


@pytest.fixture
def gated_project(tmp_path: Path) -> Path:
    """A project whose composed role binds a REFUSING gate to both roads.

    Declared under the project's own ``libraries/`` — the local root
    ``build_library_paths`` appends last, so no env override is needed and the
    builtin library stays merely additive.
    """
    project = tmp_path / "project-A"
    (project / ".agent").mkdir(parents=True)
    (project / "ai-hats.yaml").write_text(PROJECT_CONFIG, encoding="utf-8")

    role_dir = project / "libraries" / "roles" / "gate-role"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(ROLE_CONFIG, encoding="utf-8")

    script = project / "libraries" / "skills" / "gate-skill" / "hooks" / "gate.sh"
    script.parent.mkdir(parents=True)
    # Non-zero on purpose: the assertion is that the gate RAN and refused.
    script.write_text("#!/bin/sh\necho 'gate refuses' >&2\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    return project


def _foreign_envelope(monkeypatch, role: str = "judge") -> None:
    """The envelope of a session belonging to ANOTHER project.

    ``role`` exists, and that is the point: a role that does not resolve makes
    the composition refuse loudly, which is the benign half of this defect.
    """
    identity = SessionIdentity(
        id="20260101-000000-1-1",
        role=role,
        provider="claude",
        project_dir=FOREIGN_PROJECT,
        session_dir=FOREIGN_PROJECT / ".agent" / "ai-hats" / "sessions" / "runs" / "s",
    )
    for key, value in identity.to_env().items():
        monkeypatch.setenv(key, value)


def _ctx(project: Path, tmp_path: Path) -> LifecycleContext:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    return LifecycleContext(
        worktree_path=worktree,
        project_dir=project,
        state_dir=tmp_path / "state",
        branch_name="task/gated",
        carry={},
        skip_hooks=False,
        legacy=False,
        deadline=Deadline.without_lock(30.0, why="test"),
    )


def _merge(project: Path, tmp_path: Path) -> None:
    from ai_hats.wt_lifecycle import HookRunningLifecycle

    HookRunningLifecycle().before_merge(_ctx(project, tmp_path))


def test_the_projects_own_gate_refuses_the_merge(gated_project, tmp_path):
    """The control: with no session at all, the gate is resolved and it refuses."""
    from ai_hats_wt.manager import WorktreeMergeAborted

    with pytest.raises(WorktreeMergeAborted):
        _merge(gated_project, tmp_path)


def test_a_foreign_session_does_not_disarm_the_merge_gate(gated_project, tmp_path, monkeypatch):
    """The defect: session B's role declares no wt binding, so project A merged
    with nothing run and nothing said."""
    from ai_hats_wt.manager import WorktreeMergeAborted

    _foreign_envelope(monkeypatch)

    with pytest.raises(WorktreeMergeAborted):
        _merge(gated_project, tmp_path)


def test_a_foreign_envelope_without_the_scalar_pin_is_foreign_too(
    gated_project, tmp_path, monkeypatch
):
    """Case C: the pin-keyed guard returns early without ``AI_HATS_PROJECT_DIR``,
    so only the envelope's own ``project_dir`` can answer here."""
    from ai_hats_wt.manager import WorktreeMergeAborted

    _foreign_envelope(monkeypatch)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)

    with pytest.raises(WorktreeMergeAborted):
        _merge(gated_project, tmp_path)


def test_this_projects_own_session_still_governs_the_merge(gated_project, tmp_path, monkeypatch):
    """The HATS-1594 half that must not regress: an own session is still the
    authority on which role composes, so its gate still fires."""
    from ai_hats_wt.manager import WorktreeMergeAborted

    own = SessionIdentity(
        id="20260101-000000-1-2",
        role="gate-role",
        provider="claude",
        project_dir=gated_project,
        session_dir=gated_project / ".agent" / "ai-hats" / "sessions" / "runs" / "s",
    )
    for key, value in own.to_env().items():
        monkeypatch.setenv(key, value)

    with pytest.raises(WorktreeMergeAborted):
        _merge(gated_project, tmp_path)


def test_a_torn_envelope_is_still_a_refusal_not_a_skip(gated_project, tmp_path, monkeypatch):
    """Scoping must not become a second way to swallow an untrustworthy
    envelope — an unreadable one still refuses rather than reading as absence."""
    from ai_hats_wt.manager import WorktreeMergeAborted

    monkeypatch.setenv(ENV_SESSION_IDENTITY, json.dumps({"v": IDENTITY_VERSION}))

    with pytest.raises(WorktreeMergeAborted):
        _merge(gated_project, tmp_path)


def _declared(project: Path):
    """What ``rack transition`` would gate this backlog with."""
    from ai_hats.rack_consumers import AiHatsCheckPort

    catalog = project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    catalog.mkdir(parents=True, exist_ok=True)
    return AiHatsCheckPort(project, catalog=catalog).check_declarations()


def test_the_projects_own_gate_is_declared_on_the_rack_road(gated_project):
    """The control for the second road."""
    declared = _declared(gated_project)

    assert [d.at for d in declared] == [("review->done",)]


def test_a_foreign_session_does_not_disarm_the_rack_gate(gated_project, monkeypatch):
    """``review → done`` is the edge this gate exists for; session B's role
    declares no rack row, so the card closed with the gate never consulted."""
    _foreign_envelope(monkeypatch)

    assert [d.at for d in _declared(gated_project)] == [("review->done",)]


def test_a_foreign_envelope_without_the_pin_does_not_disarm_the_rack_gate(
    gated_project, monkeypatch
):
    _foreign_envelope(monkeypatch)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)

    assert [d.at for d in _declared(gated_project)] == [("review->done",)]


#: The channel entries whose ``identity`` defaults to reading the ambient
#: environment. Every production caller must say which project it is gating.
#: ``resolve_carried_rows`` joined at HATS-1682: the rack's carrier moved onto
#: it, and omitting it would retire the ratchet from that caller in silence.
_SCOPED_RESOLVERS = ("resolve_checks_at", "resolve_carried_checks", "resolve_carried_rows")


def _production_calls():
    """Every call to a scoped resolver outside the channel and outside tests."""
    import ast

    root = Path(__file__).resolve().parents[1]
    trees = [*(root / "src").rglob("*.py"), *root.glob("packages/*/src/**/*.py")]
    for path in trees:
        if path.name == "check_resolve.py":  # the channel declares them
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in _SCOPED_RESOLVERS:
                yield path.relative_to(root), name, {kw.arg for kw in node.keywords}


def test_no_production_caller_leaves_the_identity_to_the_ambient_environment():
    """The ratchet. Both roads were fixed by passing ``identity=``; a third
    caller that forgets it is the same defect again, and nothing else would
    catch it — the default is silent and resolves to *some* answer."""
    ambient = [
        f"{path}:{name}" for path, name, kwargs in _production_calls() if "identity" not in kwargs
    ]

    assert ambient == [], f"these resolve gates under whatever session ran them: {ambient}"


def test_the_ratchet_is_looking_at_something():
    """A scan that finds nothing passes vacuously forever."""
    assert len(list(_production_calls())) >= 3


def test_the_point_name_is_the_bare_one(gated_project):
    """Guard against the card's own typo: the card cites ``wt:pre-merge`` and
    with that spelling nothing resolves for an unrelated reason, so a repro
    written from it looks fixed while the defect stands."""
    assert WT_PRE_MERGE == "pre-merge"
