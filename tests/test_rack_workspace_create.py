"""HATS-1596: HYP/PROP card CREATE goes through the kernel, like every other write.

``_create_card`` used to hand-roll the card: its own alloc lock (with no timeout,
so a contended `reflect issue --bg` hung forever while `rack hyp create` failed
loudly at 30s), its own id allocation, and a direct ``task.yaml`` write that
skipped the write-strict schema, the topology's initial state, and link
validation. These pin what delegating to ``kernel.create`` buys back.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import re
from pathlib import Path

import pytest
from filelock import FileLock

from ai_hats.paths.constants import ENV_AI_HATS_DIR, PROJECT_CONFIG
from ai_hats.rack_workspace import (
    create_hypothesis,
    create_proposal,
    ensure_backlog,
    rack_workspace,
)
from ai_hats_rack.cardschema import FieldValidationError
from ai_hats_rack.kernel import LockTimeoutError
from ai_hats_rack.models import TaskCard
from ai_hats_rack.workspace import Workspace


@pytest.fixture(autouse=True)
def _no_env_optin(monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)


def _project(tmp_path: Path, *backlogs: str) -> Path:
    (tmp_path / PROJECT_CONFIG).write_text("schema_version: 4\nprovider: claude\n")
    for name in backlogs:
        ensure_backlog(ProjectLayout.at(tmp_path), name)
    return tmp_path


def _catalog(project: Path, name: str) -> Path:
    return ProjectLayout.at(project).tracker.tasks_dir.parent / name


def _card(project: Path, name: str, card_id: str) -> TaskCard:
    return TaskCard.from_yaml(_catalog(project, name) / card_id / "task.yaml")


# ----- initial state comes from the topology, not a literal --------------------


def test_hypothesis_takes_its_initial_state_from_the_backlog(tmp_path):
    """A project that renamed its initial state used to get cards in a state its
    own FSM does not have — from a command that printed "created"."""
    project = _project(tmp_path, "hypotheses")
    path = _catalog(project, "hypotheses") / "backlog.yaml"
    path.write_text(path.read_text().replace("active", "proposed"))

    hyp_id = create_hypothesis(
        rack_workspace(ProjectLayout.at(project)),
        title="t",
        hypothesis="h",
        source_task="supervisor-observation",
    )
    assert _card(project, "hypotheses", hyp_id).state == "proposed"


def test_proposal_takes_its_initial_state_from_the_backlog(tmp_path):
    project = _project(tmp_path, "proposals")
    path = _catalog(project, "proposals") / "backlog.yaml"
    # \b so the `reopen` edge name is left alone — only the STATE is renamed.
    path.write_text(re.sub(r"\bopen\b", "triage", path.read_text()))

    prop_id = create_proposal(
        rack_workspace(ProjectLayout.at(project)),
        title="t",
        category="process",
        target="x",
        description="d",
        rationale="r",
    )
    assert _card(project, "proposals", prop_id).state == "triage"


# ----- the write-strict schema applies here too --------------------------------


def test_proposal_with_an_undeclared_category_is_refused(tmp_path):
    """`category` declares its choices; the hand-rolled writer wrote any string."""
    project = _project(tmp_path, "proposals")
    with pytest.raises(FieldValidationError):
        create_proposal(
            rack_workspace(ProjectLayout.at(project)),
            title="t",
            category="not-a-declared-choice",
            target="x",
            description="d",
            rationale="r",
        )
    assert not list(_catalog(project, "proposals").glob("PROP-*/task.yaml"))


# ----- links survive the delegation --------------------------------------------


def test_hypothesis_links_a_real_source_task(tmp_path):
    project = _project(tmp_path, "hypotheses")
    ws = rack_workspace(ProjectLayout.at(project))
    real = ws.kernel_for("HATS-1").create(actor="t", caller_cwd=project, title="anchor").task.id

    hyp_id = create_hypothesis(ws, title="t", hypothesis="h", source_task=real)
    assert _card(project, "hypotheses", hyp_id).links["source_task"] == [real]


def test_proposal_links_its_related_hypotheses(tmp_path):
    project = _project(tmp_path, "hypotheses", "proposals")
    ws = rack_workspace(ProjectLayout.at(project))
    hyp_id = create_hypothesis(ws, title="h", hypothesis="h")

    prop_id = create_proposal(
        ws,
        title="t",
        category="process",
        target="x",
        description="d",
        rationale="r",
        related_hypotheses=[hyp_id],
    )
    assert _card(project, "proposals", prop_id).links["related_hypotheses"] == [hyp_id]


# ----- one lock file, one policy -----------------------------------------------


def test_a_contended_alloc_lock_fails_loudly_instead_of_waiting_forever(tmp_path, monkeypatch):
    """The reported symptom. This road built its own ``FileLock`` with no timeout
    — filelock's default ``-1`` blocks forever — over the SAME ``.alloc.lock`` the
    kernel bounds at 30s, so a detached ``reflect issue --bg`` hung silently where
    ``rack hyp create`` refused out loud.

    What is pinned here is the policy, not the hang: contention must end in a
    bounded, typed ``LockTimeoutError``. (Same-process contention is the rack's own
    ``test_alloc_lock_timeout_is_loud`` methodology; filelock's deadlock guard
    means only a second PROCESS reproduces the literal wait.)
    """  # comment-length: allow — what the pin does and does not prove
    project = _project(tmp_path, "hypotheses")
    build = Workspace.kernel_for_instance

    def short_timeout(self, instance):
        kernel = build(self, instance)
        kernel._lock_timeout = 0.2  # the policy under test is finite-vs-infinite
        return kernel

    monkeypatch.setattr(Workspace, "kernel_for_instance", short_timeout)

    with FileLock(str(_catalog(project, "hypotheses") / ".alloc.lock")):
        with pytest.raises(LockTimeoutError, match="task-id allocation"):
            create_hypothesis(rack_workspace(ProjectLayout.at(project)), title="t", hypothesis="h")


# ----- a stale --task id demotes instead of writing a dangling edge -------------


def test_a_source_task_that_does_not_exist_demotes_to_origin(tmp_path):
    """`_is_card_id` only checked that the PREFIX routes somewhere, so a stale id
    was written as a link the kernel would have refused. The intake observation is
    worth keeping, so the id lands in `origin` — the channel a non-id sentinel
    already used — rather than failing the whole command."""
    project = _project(tmp_path, "hypotheses")
    hyp_id = create_hypothesis(
        rack_workspace(ProjectLayout.at(project)),
        title="t",
        hypothesis="h",
        source_task="HATS-99999",
    )
    card = _card(project, "hypotheses", hyp_id)
    assert "source_task" not in card.links
    assert card.extras["origin"] == "HATS-99999"


def test_an_explicit_origin_survives_a_stale_source_task(tmp_path):
    """Demotion must not clobber an origin the caller set on purpose."""
    project = _project(tmp_path, "hypotheses")
    hyp_id = create_hypothesis(
        rack_workspace(ProjectLayout.at(project)),
        title="t",
        hypothesis="h",
        source_task="HATS-99999",
        origin="supervisor-observation",
    )
    assert _card(project, "hypotheses", hyp_id).extras["origin"] == "supervisor-observation"
