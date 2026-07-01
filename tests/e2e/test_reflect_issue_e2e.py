"""E2E: ``ai-hats reflect issue "..."`` drafts an HYP for the user.

User-way (HATS-546 / S-CLI-27)
------------------------------

I'm in the middle of (or just finished) work. I notice a recurring
agent failure mode — e.g. "the agent caches stale state across
sub-agent spawns". I want to record this as a hypothesis to
investigate later, without context-switching to manually open a YAML
file. I type:

    ai-hats reflect issue "agent caches stale state across spawns"

The command runs briefly (it's a Haiku call). A draft HYP file lands
under ``<tracker>/hypotheses/HYP-NNN.yaml`` with my observation
captured in the ``hypothesis`` field, populated metadata, and
``status: active``. I keep working — no further interaction needed.

What this test pins
-------------------

1. The default mode (no ``--preview``, no ``--bg``) writes
   immediately on success.
2. Exactly ONE new ``HYP-NNN.yaml`` lands in the tracker dir.
3. The YAML payload has the user-facing contract fields populated:
   ``title``, ``hypothesis``, ``source_task``, ``status == "active"``.

What this test does NOT pin
---------------------------

- The LLM-generated wording inside ``title`` / ``hypothesis`` —
  haiku phrasing is not deterministic; we only assert non-emptiness.
- ``--preview`` (interactive confirm) — separate user-flow.
- ``--bg`` (detached) — separate user-flow.
- ``MergeAction`` path (existing HYP is appended) — current project
  has no active HYPs, so haiku will deterministically pick
  ``CreateAction``.

Fixture choice: ``tmp_project`` (dev venv binary + real HOME claude
auth). Same rationale as HATS-545 / S-CLI-04 — the launcher-venv
build refuses to install when the worktree branch is ahead of master
(separate framework gap).

Deliberate long e2e scenario contract — noqa: comment-length.
"""

from __future__ import annotations

import pytest
import yaml

from _helpers.project import Project


pytestmark = pytest.mark.integration


# Concrete observation text. Haiku will paraphrase into the draft's
# title + hypothesis fields. The literal phrase isn't asserted (LLM
# wording isn't deterministic) — only structural fields.
OBSERVATION = "agent caches stale state across sub-agent spawns"

# Haiku one-turn + harness overhead is ~5-15s; envelope buffer for
# slow networks / cold-start.
ISSUE_TIMEOUT = 90.0


def test_reflect_issue_writes_a_draft_hypothesis(
    tmp_project: Project,
    requires_claude_auth,  # noqa: ARG001 — skip-marker fixture
) -> None:
    """User runs ``reflect issue "..."`` → one new HYP file on disk."""
    hyp_dir = tmp_project.agent_dir / "tracker" / "hypotheses"
    # Baseline snapshot — tmp_project is role-less + fresh, so the
    # dir may not even exist yet. ``reflect issue`` creates it via
    # ``store.dir.mkdir(parents=True, exist_ok=True)``.
    before = (
        {p.name for p in hyp_dir.glob("HYP-*.yaml")}
        if hyp_dir.exists() else set()
    )

    tmp_project.run(
        "reflect", "issue", OBSERVATION,
        timeout=ISSUE_TIMEOUT,
    ).expect_ok()

    assert hyp_dir.is_dir(), (
        f"hypotheses dir not created at {hyp_dir} — "
        "did reflect issue silently no-op?"
    )

    after = {p.name for p in hyp_dir.glob("HYP-*.yaml")}
    new = after - before
    assert len(new) == 1, (
        f"expected exactly one new HYP file under {hyp_dir}, "
        f"got {len(new)} new: {sorted(new)}"
    )

    new_path = hyp_dir / next(iter(new))
    data = yaml.safe_load(new_path.read_text())
    assert isinstance(data, dict), (
        f"HYP {new_path} not a YAML mapping: {type(data).__name__}"
    )

    # User-contract fields populated by ``_write_intake`` (cli/reflect.py).
    # Empty-string is treated as missing — haiku must produce SOMETHING.
    assert data.get("title"), (
        f"empty/missing title in {new_path}: {data}"
    )
    assert data.get("hypothesis"), (
        f"empty/missing hypothesis in {new_path}: {data}"
    )
    # ``source_task`` defaults to ``"supervisor-observation"`` when
    # ``--task`` is absent (cli/reflect.py:615 + :462
    # SUPERVISOR_SOURCE_TASK constant).
    assert data.get("source_task"), (
        f"missing source_task in {new_path}: {data}"
    )
    assert data.get("status") == "active", (
        f"unexpected status {data.get('status')!r} in {new_path}: {data}"
    )
