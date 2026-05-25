"""E2E test of the bare ``ai-hats`` HITL flow + auto-retro session-reviewer.

Validates five orthogonal claims (HATS-498):

1. The right role is composed into the HITL child claude process.
2. Customization layering works end-to-end — BOTH global-layer
   (``~/.ai-hats/customizations.yaml``) AND project-layer
   (``<project>/ai-hats.yaml``) entries reach the materialized prompt
   with correct ``provenance`` tagging.
3. The composed prompt actually reaches the child claude (magic-word
   proxy — HATS-452 / HATS-501 regression guard).
4. After the HITL session, the auto-retro session-reviewer spawns
   with the correct role and emits draft HYP verdicts + PROP actions
   into the tracker.

Implementation as five composable phases, each a helper function with
an explicit dataclass contract — extension points marked per phase.

Cost shape: ~46s shared venv (amortised) + ~5s Phase 1 + ~10-15s HITL
turn + ~15-25s reviewer subagent = ~60-90s post-venv. ~$0.04 per run
on haiku-4-5; cost cap asserted at $0.10 envelope (Phase 5).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from _helpers.project import Project


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test fixture identifiers — local to this file by design.
#
# Each sibling Wave 2 e2e test will pick its own role / trait / marker
# combination depending on what surface it exercises (per HATS-498 plan
# §Phases — composable blocks). Extracting these to a shared
# ``tests/e2e/_helpers/config.py`` would freeze ONE choice for every
# future test; defer the extraction until a second consumer makes the
# real shared set obvious (design-minimalism: justify primitives by
# concrete use cases in the current epic, not speculative reuse).
# ---------------------------------------------------------------------------


# Magic token claude is instructed to echo as the first response token
# in every reply. Used as the claim #3 proxy: presence in
# ``transcript.txt`` proves the composed prompt reached the child
# claude AND was applied. Random per-run so a stale token leaking from
# any prior state (e.g. dev's manual /tmp dirs, debug logs) cannot
# false-positive the assertion. Length 8 hex = 32-bit collision space
# — overkill for a single-test fixture but cheap.
def _new_magic_token() -> str:
    return f"RESPONSE-{secrets.token_hex(4).upper()}"


def _injection_append_text(magic_token: str) -> str:
    return (
        f"TEST-MAGIC: As your very first response token in every reply, "
        f"emit the literal token {magic_token} verbatim before anything else."
    )


# Traits seeded at the global / project layers respectively. Chosen
# because their ``injection`` content provides stable grep-able
# markers (``## SHELL DEVELOPMENT`` / ``## PYTHON DEVELOPMENT``) and
# they don't overlap with the maintainer base trait stack. These are
# documented user-facing traits in ``library/usage/traits/dev/`` — if
# they're ever renamed / removed, ``config customize --add-trait``
# rejects the unknown name before any LLM call and Phase 1 fails loud
# with a clear error (good — that rename IS a real breaking change).
GLOBAL_TRAIT = "dev::shell"
GLOBAL_TRAIT_MARKER = "## SHELL DEVELOPMENT"

PROJECT_TRAIT = "dev::python"
PROJECT_TRAIT_MARKER = "## PYTHON DEVELOPMENT"

# Reviewer model pin — paired with cost-cap (Phase 5). No CLI flag
# exists for ``feedback.session_retro.review_model`` (verified at
# pre-freeze), so Phase 1 writes this via direct yaml edit.
REVIEW_MODEL = "claude-haiku-4-5"

# Subprocess timeouts. Non-LLM ai-hats commands should complete in
# under a second on a warm interpreter; ``self init`` includes the
# heavier bootstrap (ai_hats import + venv check + tree mkdir) so it
# gets a larger budget. If any of these caps actually fire, it's a
# signal worth investigating, not a number worth raising blindly.
SELF_INIT_TIMEOUT = 15.0
CMD_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Phase contracts (dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetupContext:
    """Static fixture state produced by Phase 1 — passed to all later phases.

    ``env`` carries the HOME-isolation env entry that MUST be threaded
    through every subsequent ``Project.run()`` invocation; otherwise
    a stray subprocess would see the developer's real
    ``~/.ai-hats/customizations.yaml`` and contaminate the test.
    """

    tmp_home: Path
    env: dict[str, str]
    hyp_id: str
    prop_id: str
    magic_token: str               # per-run random; see _new_magic_token()
    global_trait: str = GLOBAL_TRAIT
    project_trait: str = PROJECT_TRAIT
    review_model: str = REVIEW_MODEL


# ---------------------------------------------------------------------------
# Phase 1 — Setup (no LLM)
# ---------------------------------------------------------------------------


def phase_setup(project: Project, tmp_path: Path) -> SetupContext:
    """Initialise project + seed customizations + tracker fixtures.

    Side effects on disk:

    - ``<project>/ai-hats.yaml`` — created by ``self init``; mutated by
      project-layer customizations + reviewer_model yaml edit.
    - ``<tmp_home>/.ai-hats/customizations.yaml`` — created by
      ``config customize ... --global`` under the isolated HOME.
    - ``<project>/.agent/ai-hats/tracker/hypotheses/HYP-NNN.yaml`` and
      ``.../backlog/proposals/PROP-NNN.yaml`` — seeded so the auto-retro
      reviewer has something to vote on (Phase 5 forcing function).

    Self-check at end:

    - ``ai-hats config show-prompt`` produces a prompt containing all
      three customization markers (project injection_append, global
      trait, project trait). If this fails, no point running the LLM —
      something in the composer / Assembler / customizations pipeline
      is broken and the failure mode is structural, not behavioural.

    Returns
    -------
    SetupContext
        The fixture handle threaded through Phases 2-5.
    """
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    env = {
        "HOME": str(tmp_home),
        # Explicit recursion-guard reset: if the dev runs the test inside
        # a session that itself spawned ai-hats with HATS_SKIP_RETRO=1
        # (auto-retro recursion guard from auto_retro.py:283), the var
        # would leak into our subprocesses and silently disable Phase 5's
        # session-reviewer spawn. Force-unset by passing an empty string,
        # which the production code treats as "not set" via the
        # ``os.environ.get("HATS_SKIP_RETRO") != "1"`` check.
        "HATS_SKIP_RETRO": "",
    }

    magic_token = _new_magic_token()
    injection_text = _injection_append_text(magic_token)

    # ----- self init: write ai-hats.yaml + create .agent/ai-hats/ tree -----
    project.run(
        "self", "init", "-r", "maintainer", "-p", "claude", "--no-update",
        timeout=SELF_INIT_TIMEOUT, extra_env=env,
    ).expect_ok().expect_stdout_contains(
        "Default role: maintainer", "Provider: claude",
    )

    # ----- lower auto-retro threshold so a 1-turn HITL triggers it -----
    project.run(
        "config", "feedback", "session-retro", "smart",
        "--threshold", "turns=1,tool_calls=0",
        timeout=CMD_TIMEOUT, extra_env=env,
    ).expect_ok()

    # ----- pin reviewer model via direct yaml edit (no CLI flag) -----
    _set_review_model(project.yaml, REVIEW_MODEL)

    # ----- global-layer trait (writes to <tmp_home>/.ai-hats/customizations.yaml) -----
    project.run(
        "config", "customize", "maintainer",
        "--add-trait", GLOBAL_TRAIT, "--global",
        timeout=CMD_TIMEOUT, extra_env=env,
    ).expect_ok()

    # ----- project-layer trait (writes to <project>/ai-hats.yaml customizations:) -----
    project.run(
        "config", "customize", "maintainer",
        "--add-trait", PROJECT_TRAIT,
        timeout=CMD_TIMEOUT, extra_env=env,
    ).expect_ok()

    # ----- project-layer injection_append (the claim #3 magic-word vehicle) -----
    project.run(
        "config", "customize", "maintainer",
        "--injection-append", injection_text,
        timeout=CMD_TIMEOUT, extra_env=env,
    ).expect_ok()

    # ----- pre-seed 1 active HYP — forces reviewer to emit a hypothesis_verdict -----
    hyp_result = project.run(
        "task", "hyp", "create",
        "--title", "test fixture: maintainer overlay claim probe",
        "--hypothesis", (
            "Under HATS-498 e2e fixture, the maintainer role's "
            "project-layer overlay reaches the materialized prompt."
        ),
        "--source-task", "HATS-498",
        "--json",
        timeout=CMD_TIMEOUT, extra_env=env,
    ).expect_ok()
    hyp_id = json.loads(hyp_result.stdout.strip().splitlines()[-1])["id"]

    # ----- pre-seed 1 open PROP — forces reviewer to emit a proposal_action -----
    prop_result = project.run(
        "task", "proposal", "create",
        "--title", "test fixture: improve maintainer overlay coverage",
        "--category", "process",
        "--target", "maintainer",
        "--description", "Test-fixture proposal seeded by HATS-498 e2e.",
        "--rationale", "Forces auto-retro reviewer to emit proposal_actions.",
        "--session", "hats-498-fixture",
        "--json",
        timeout=CMD_TIMEOUT, extra_env=env,
    ).expect_ok()
    prop_id = json.loads(prop_result.stdout.strip().splitlines()[-1])["id"]

    # ----- Phase 1 self-check: show-prompt surfaces all 3 customization layers -----
    #
    # No-LLM gate. If any marker is missing here, the composer /
    # Assembler / customizations resolver is broken and there's no
    # value in spending $$ on the HITL turn.
    project.run(
        "config", "show-prompt",
        timeout=CMD_TIMEOUT, extra_env=env,
    ).expect_ok().expect_stdout_contains(
        magic_token,
        GLOBAL_TRAIT_MARKER,
        PROJECT_TRAIT_MARKER,
    )

    return SetupContext(
        tmp_home=tmp_home,
        env=env,
        hyp_id=hyp_id,
        prop_id=prop_id,
        magic_token=magic_token,
    )


def _set_review_model(yaml_path: Path, model: str) -> None:
    """Patch ``feedback.session_retro.review_model`` in ai-hats.yaml.

    Preserves all other config; raises if the file doesn't exist (Phase
    1 calls this AFTER ``self init``, so absence is a programmer error).
    """
    data = yaml.safe_load(yaml_path.read_text())
    feedback = data.setdefault("feedback", {})
    session_retro = feedback.setdefault("session_retro", {})
    session_retro["review_model"] = model
    yaml_path.write_text(yaml.safe_dump(data, sort_keys=False))


# ---------------------------------------------------------------------------
# Phase 2 — Drive HITL session (TODO — separate phase commit)
# ---------------------------------------------------------------------------
# def phase_drive_hitl(project, ctx) -> HitlDriveResult: ...


# ---------------------------------------------------------------------------
# Phase 3 — Identify HITL session_dir + assert HITL claims (TODO)
# ---------------------------------------------------------------------------
# def phase_assert_hitl_session(project, ctx) -> HitlSession: ...


# ---------------------------------------------------------------------------
# Phase 4 — Identify reviewer session_dir + assert role (TODO)
# ---------------------------------------------------------------------------
# def phase_assert_reviewer_session(project, ctx, hitl) -> ReviewerSession: ...


# ---------------------------------------------------------------------------
# Phase 5 — Assert retro.md + draft artefacts (TODO)
# ---------------------------------------------------------------------------
# def phase_assert_retro_artefacts(project, ctx, hitl) -> None: ...


# ---------------------------------------------------------------------------
# Test body — wires the phases together.
# ---------------------------------------------------------------------------


def test_hitl_role_overlay_prompt_then_auto_retro_spawns_auditor(
    tmp_venv_project: Project,
    requires_claude_auth,  # noqa: ARG001 — skip-marker fixture
    tmp_path: Path,
) -> None:
    """Bare HITL flow + auto-retro auditor (claim mapping in module docstring)."""

    ctx = phase_setup(tmp_venv_project, tmp_path)

    # ----- Phase 2-5 — TODO, separate commits per HATS-498 plan -----
    assert ctx.hyp_id.startswith("HYP-"), ctx
    assert ctx.prop_id.startswith("PROP-"), ctx
    assert (ctx.tmp_home / ".ai-hats" / "customizations.yaml").exists(), ctx
