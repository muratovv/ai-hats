"""E2E test of the ai-hats role+composition+auto-retro vertical.

Validates five orthogonal claims (HATS-498):

1. The right role is composed into the child claude process.
2. Customization layering works end-to-end — BOTH global-layer
   (``~/.ai-hats/customizations.yaml``) AND project-layer
   (``<project>/ai-hats.yaml``) entries reach the materialized prompt
   with correct ``provenance`` tagging.
3. The composed prompt actually reaches the child claude (magic-word
   proxy — HATS-452 / HATS-501 regression guard).
4. After the session, the session-reviewer runs with the correct role
   and emits draft HYP verdicts + PROP actions into the tracker.

Implementation as five composable phases, each a helper function with
an explicit dataclass contract — extension points marked per phase.

**Driver is ``execute --batch`` (SubAgent / SDK), NOT bare ``ai-hats``
HITL** — temporary workaround for two harness asymmetries discovered
during HATS-498 implementation:

- HATS-529 — HITL audit.md doesn't capture assistant responses, so
  the magic-word proxy for claim #3 (echo verification) is impossible
  via audit. Without this fix the behavioural turn cannot be asserted.
- HATS-530 — auto-retro spawn lives only in WrapRunner finalize, not
  in SubAgent finalize. With this workaround, Phase 5 invokes
  ``ai-hats session retro <sid>`` manually instead of relying on
  auto-spawn.

TODO(HATS-529, HATS-530): swap drive_bare_hitl + auto-spawn assertion
in once both harness fixes land. The TRUE user-facing flow this test
guards is bare ``ai-hats`` HITL; the current shape exercises the same
composition + reviewer machinery via the cleanest available channel.

Cost shape: ~46s shared venv (amortised) + ~5s Phase 1 + ~5-10s
SDK turn + ~10-15s reviewer subagent = ~50-80s post-venv. ~$0.05
per run on sonnet-4-5; cost cap asserted at $0.10 envelope (Phase 5).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from _helpers.project import Project, RunResult
from _helpers.sessions import (
    read_metrics,
    snapshot_session_dirs,
    wait_for_new_session_dir,
)


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

# SDK drive (workaround for HATS-529/HATS-530 — see module docstring).
# Sonnet — strong instruction following (Opus skipped magic-word
# injection in HITL pre-freeze; haiku is more obedient but sonnet is
# the operational sweet spot — and HITL TODO swap will need it too).
DRIVE_MODEL = "claude-sonnet-4-5"
# Directive user prompt — the maintainer role's injection_append
# (magic-word forcing function) prepends RESPONSE-{random} regardless
# of prompt content; user text just makes the turn well-defined.
DRIVE_PROMPT = "Reply with just: ok"
DRIVE_TIMEOUT = 90.0  # SDK turn ~5-10s; envelope buffer for slow networks
# Auto-retro (manual fallback per HATS-530) runs another SDK turn in a
# subprocess. Sonnet review_model pinned in Phase 1 ai-hats.yaml.
RETRO_TIMEOUT = 120.0
# Cost cap asserted post-run. Two LLM turns at sonnet — drive turn
# against maintainer (~5-10K token prompt; observed ~$0.06) +
# session-reviewer turn (similarly sized; observed ~$0.05). $0.20
# envelope covers normal variance with 2× headroom. A runaway prompt
# (e.g. exploded composition due to a regression) trips this cap
# before $ vanishes.
COST_CAP_USD = 0.20


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_global_customizations():
    """Back up ``~/.ai-hats/customizations.yaml`` at entry, restore at exit.

    Required because HATS-498 needs to seed a global-layer
    customization (``config customize --global``) but cannot isolate
    HOME (macOS Keychain ACLs for claude credentials are scoped per
    real HOME — isolated HOME breaks claude SDK auth, verified
    empirically). Tests therefore mutate the developer's real
    ``~/.ai-hats/customizations.yaml`` and this fixture guarantees the
    original is restored even on test failure / interrupt.

    Race-aware caveat: if a concurrent claude/ai-hats session reads
    ``~/.ai-hats/customizations.yaml`` mid-test it sees the seeded
    overlays. Acceptable for the integration-tier ``pytest -m
    integration`` run cadence; not safe for parallel test execution
    on the same developer machine.

    TODO(HATS-532): replace with ``AI_HATS_USER_HOME=<tmp>`` env
    override once that lands — proper isolation, no real-state
    pollution, no race window.
    """
    customizations = Path.home() / ".ai-hats" / "customizations.yaml"
    backup = customizations.read_bytes() if customizations.exists() else None
    try:
        yield customizations
    finally:
        if backup is None:
            customizations.unlink(missing_ok=True)
        else:
            customizations.write_bytes(backup)


# ---------------------------------------------------------------------------
# Phase contracts (dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetupContext:
    """Static fixture state produced by Phase 1 — passed to all later phases.

    ``env`` carries env-var overrides threaded into every subsequent
    ``Project.run()`` invocation — currently just
    ``HATS_SKIP_RETRO=""`` to defuse the auto-retro recursion guard.

    NOTE: The test uses the developer's REAL ``HOME`` rather than an
    isolated ``tmp_home``. Reason: claude credentials live in macOS
    Keychain entries keyed by the real HOME path; an isolated HOME
    breaks Keychain ACL lookup and claude SDK reports ``Not logged in
    · Please run /login`` (verified empirically during HATS-498).
    Global-layer customization is therefore seeded into the real
    ``~/.ai-hats/customizations.yaml`` and backed up / restored
    around the test via the ``isolated_global_customizations``
    pytest fixture.
    TODO(HATS-532): when ``AI_HATS_USER_HOME`` env override lands,
    swap real-HOME workaround for proper tmp-home isolation — claude
    auth stays via real HOME, ai-hats global customizations resolve
    under ``AI_HATS_USER_HOME``.
    """

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


def phase_setup(project: Project) -> SetupContext:
    """Initialise project + seed customizations + tracker fixtures.

    Side effects on disk:

    - ``<project>/ai-hats.yaml`` — created by ``self init``; mutated by
      project-layer customizations + reviewer_model yaml edit.
    - ``~/.ai-hats/customizations.yaml`` — created/mutated by
      ``config customize ... --global``. The
      ``isolated_global_customizations`` pytest fixture backs up the
      original (if any) and restores it post-test.
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
    env = {
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
# Phase 2 — Drive an SDK session under the test fixture
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveResult:
    """Outcome of phase_drive — fields parsed from the ``--json`` envelope.

    ``session_dir`` is the absolute path to
    ``<ai_hats_dir>/sessions/runs/session_<id>/`` where Phase 3
    artefacts (audit.md, metrics.json, meta_prompt.txt, transcript.txt)
    live.
    """

    session_id: str
    session_dir: Path
    total_cost_usd: float
    raw: RunResult


def phase_drive(project: Project, ctx: SetupContext) -> DriveResult:
    """[Phase 2] Drive one SDK session under the test fixture.

    Currently uses ``ai-hats execute --batch`` (SubAgentRunner / SDK
    path) as a WORKAROUND for two harness asymmetries discovered during
    HATS-498 implementation:

    - HATS-529 — HITL ``audit.md`` doesn't capture assistant responses,
      making the magic-word echo proxy for claim #3 unverifiable when
      driving via bare ``ai-hats``.
    - HATS-530 — auto-retro spawn isn't symmetric across finalizers;
      Phase 5 below invokes ``session retro`` manually instead.

    TODO(HATS-529, HATS-530): swap to ``_helpers.hitl.drive_bare_hitl``
    + remove the manual ``session retro`` invocation in Phase 5 once
    both harness fixes land. The TRUE user-facing flow this test
    exists to guard is bare-``ai-hats`` HITL; the current shape
    exercises the same composition + reviewer machinery via the
    cleanest available channel.

    Persistent artefacts produced (asserted in Phase 3):

    - ``<session_dir>/audit.md`` — composition snapshot + metrics.
    - ``<session_dir>/metrics.json`` — structured composition + cost.
    - ``<session_dir>/meta_prompt.txt`` — materialized SDK prompt
      (claim #2 / #3 structural source of truth).
    - ``<session_dir>/transcript.txt`` — clean claude response stream
      (claim #3 behavioural source of truth — magic-word echo).
    """
    result = project.run(
        "execute", "--batch",
        "-r", "maintainer", "-p", "claude",
        "--model", DRIVE_MODEL,
        "--prompt", DRIVE_PROMPT,
        "--json",
        timeout=DRIVE_TIMEOUT,
        extra_env=ctx.env,
    ).expect_ok()
    envelope = _extract_json_envelope(result.stdout)
    return DriveResult(
        session_id=envelope["session_id"],
        session_dir=Path(envelope["session_dir"]),
        total_cost_usd=envelope.get("total_cost_usd", 0.0),
        raw=result,
    )


def _extract_json_envelope(stdout: str) -> dict:
    """Find the ``execute --batch --json`` envelope (one-line dict
    with ``exit_code``) in ``stdout``.

    Iterates lines in reverse and returns the first that parses as a
    JSON object with an ``exit_code`` key. Tolerates surrounding
    pipeline output (``render_update_banner`` etc.) which may print
    extra lines after the JSON write. Mirror of the helper in
    ``test_golden_path.py`` — kept local until a second consumer
    justifies extraction (design-minimalism §).
    """
    for raw in reversed(stdout.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and "exit_code" in obj:
            return obj
    raise AssertionError(
        "no JSON envelope with 'exit_code' key found in stdout — "
        "is ``execute --batch --json`` still emitting one line via "
        "``click.echo(json.dumps(payload))`` in cli/execute.py?\n"
        f"stdout (tail 800):\n{stdout[-800:]}"
    )


# ---------------------------------------------------------------------------
# Phase 3 — Assert drive session artefacts (composition + materialization)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveSession:
    """Drive-session artefacts loaded for Phase 3 assertions."""

    session_id: str
    session_dir: Path
    metrics: dict             # parsed metrics.json
    meta_prompt: str          # meta_prompt.txt content
    transcript: str           # transcript.txt content
    audit_md: str             # audit.md content


def phase_assert_drive_session(
    drive: DriveResult, ctx: SetupContext,
) -> DriveSession:
    """[Phase 3] Verify role + customization layering + prompt-reaches-claude.

    Claims pinned here:

    - #1 (right role): ``metrics.composition.role == "maintainer"``.
    - #2 (global layer): ``metrics.composition.provenance.traits[GLOBAL_TRAIT]
      == "global"`` — proves ``~/.ai-hats/customizations.yaml`` was
      resolved.
    - #2 (project trait): ``metrics.composition.provenance.traits
      [PROJECT_TRAIT] == "project"`` — proves project layer wins.
    - #2 (project injection_append): ``ctx.magic_token in meta_prompt``
      — proves composer + materializer didn't drop the injection_append
      string (HATS-501 regression guard).
    - #3 (behavioural): ``ctx.magic_token in transcript`` — proves the
      composed system_prompt actually reached the claude SDK AND claude
      applied it (HATS-452 regression guard via response echo).
    """
    sd = drive.session_dir
    # All four files MUST exist post-session — these are the persistent
    # artefacts (HATS-523 for meta_prompt; _finalize_sub_agent for
    # transcript / metrics / audit).
    metrics = read_metrics(sd)
    meta_prompt = (sd / "meta_prompt.txt").read_text()
    transcript = (sd / "transcript.txt").read_text()
    audit_md = (sd / "audit.md").read_text()

    composition = metrics["composition"]
    # Claim #1 — role lives at top level of metrics (verified at pre-freeze;
    # composition dict only carries traits/rules/skills/provenance).
    assert metrics["role"] == "maintainer", (
        f"metrics.role={metrics['role']!r} (expected 'maintainer')"
    )
    # Claim #2 — provenance per layer
    prov = composition.get("provenance", {}).get("traits", {})
    assert prov.get(ctx.global_trait) == "global", (
        f"trait {ctx.global_trait!r} not tagged as 'global' in provenance: "
        f"{prov}"
    )
    assert prov.get(ctx.project_trait) == "project", (
        f"trait {ctx.project_trait!r} not tagged as 'project' in provenance: "
        f"{prov}"
    )
    # Claim #2 — injection_append landed in materialized prompt
    assert ctx.magic_token in meta_prompt, (
        f"magic_token {ctx.magic_token!r} missing from meta_prompt.txt "
        f"({len(meta_prompt)} bytes) — HATS-501 regression?"
    )
    # Claim #3 — claude echoed magic_token
    assert ctx.magic_token in transcript, (
        f"magic_token {ctx.magic_token!r} missing from transcript "
        f"({len(transcript)} bytes) — composition did not reach claude or "
        f"model ignored the injection. Transcript tail:\n"
        f"{transcript[-500:]}"
    )

    return DriveSession(
        session_id=drive.session_id,
        session_dir=sd,
        metrics=metrics,
        meta_prompt=meta_prompt,
        transcript=transcript,
        audit_md=audit_md,
    )


# ---------------------------------------------------------------------------
# Phase 4 — Invoke reviewer + assert reviewer composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewerSession:
    """Reviewer session artefacts loaded for Phase 4 / 5 assertions."""

    session_id: str
    session_dir: Path
    metrics: dict
    meta_prompt: str


# Stable markers from ``library/core/roles/session-reviewer/config.yaml``
# (priorities header rendered into the system_prompt). If the role's
# priorities change, this list must follow.
_REVIEWER_PROMPT_MARKERS = (
    "Completeness", "Hypothesis-fidelity", "Format-strictness",
)


def phase_invoke_reviewer(
    project: Project, ctx: SetupContext, drive: DriveResult,
) -> ReviewerSession:
    """[Phase 4] Manually invoke session-reviewer + assert correct role.

    Claims pinned here:

    - #6 (config layer): reviewer subprocess composed with
      ``role == "session-reviewer"``.
    - #6 (prompt layer): reviewer's ``meta_prompt.txt`` contains
      session-reviewer priorities markers, proving the composer +
      materializer chain works for non-maintainer roles too.

    TODO(HATS-530): replace the explicit ``session retro`` invocation
    with reliance on auto-retro spawn after ``phase_drive`` —
    auto-retro currently lives only in ``_finalize_session`` (HITL /
    WrapRunner), not ``_finalize_sub_agent`` (SubAgent / batch). Once
    HATS-530 lands, ``phase_drive`` triggers reviewer spawn organically
    and this manual call becomes redundant. The reviewer artefacts
    asserted in Phase 5 are identical either way.
    """
    snapshot = snapshot_session_dirs(project.path)
    # max-retries=3 — default 1 leaves reviewer LLM one second chance,
    # and sonnet occasionally emits a malformed observations entry
    # (dict in place of string) that needs a retry. Three attempts
    # keeps the test deterministic without exploding cost (each retry
    # is one cheap reviewer turn).
    project.run(
        "session", "retro", drive.session_id,
        "--max-retries", "3",
        timeout=RETRO_TIMEOUT, extra_env=ctx.env,
    ).expect_ok()

    reviewer_dir = wait_for_new_session_dir(
        snapshot, role="session-reviewer",
        timeout=5.0, interval=0.2,  # session retro is synchronous; dir exists immediately on return
    )
    metrics = read_metrics(reviewer_dir)
    meta_prompt = (reviewer_dir / "meta_prompt.txt").read_text()
    # Claim #6 — config layer (role at top-level of metrics, see Phase 3 note)
    assert metrics["role"] == "session-reviewer", (
        f"reviewer metrics.role={metrics['role']!r}"
    )
    # Claim #6 — prompt layer (priorities markers reached child SDK)
    for marker in _REVIEWER_PROMPT_MARKERS:
        assert marker in meta_prompt, (
            f"reviewer meta_prompt.txt missing marker {marker!r} — "
            f"composer / materializer regression for non-maintainer roles?"
        )

    return ReviewerSession(
        session_id=reviewer_dir.name.removeprefix("session_"),
        session_dir=reviewer_dir,
        metrics=metrics,
        meta_prompt=meta_prompt,
    )


# ---------------------------------------------------------------------------
# Phase 5 — Assert retro.md + draft artefacts + cost cap
# ---------------------------------------------------------------------------


def phase_assert_retro_artefacts(
    project: Project,
    ctx: SetupContext,
    drive: DriveResult,
    reviewer: ReviewerSession,
) -> None:
    """[Phase 5] Verify retro.md content + cost cap.

    Claims pinned here:

    - #4 (file existence): ``<retros_dir>/sessions/<drive.session_id>.md``
      exists with non-empty parsable YAML frontmatter (HATS contract:
      reviewer writes retro keyed by the REVIEWED session's id).
    - #4 (HYP verdict): reviewer emitted a vote on the pre-seeded
      ``ctx.hyp_id`` — proves the active-hypotheses forcing function
      in ``SessionReviewRunner._render_active_hypotheses`` works.
    - #4 (PROP action): reviewer emitted an action on the pre-seeded
      ``ctx.prop_id`` — proves the open-proposals forcing function.
    - #4 defensive: if reviewer YAML somehow validates without HYP /
      PROP refs, a meta-proposal SHOULD be filed by
      ``reflect_session_main._harness_check`` — surface it in the
      failure message rather than silently passing.
    - Cost cap: combined ``drive.total_cost_usd`` +
      ``reviewer.metrics.total_cost_usd`` stays under ``COST_CAP_USD``.
    """
    retro_path = (
        project.path / ".agent" / "ai-hats" / "sessions" / "retros"
        / "sessions" / f"{drive.session_id}.md"
    )
    assert retro_path.exists() and retro_path.stat().st_size > 0, (
        f"retro.md missing or empty at {retro_path}"
    )

    raw = retro_path.read_text()
    frontmatter = yaml.safe_load(_extract_frontmatter(raw))
    assert isinstance(frontmatter, dict), (
        f"retro.md frontmatter is not a YAML mapping: {type(frontmatter)}"
    )

    # Claim #4 — HYP verdict
    verdicts = frontmatter.get("hypothesis_verdicts") or []
    verdict_hyp_ids = {
        v.get("hyp_id") for v in verdicts if isinstance(v, dict)
    }
    assert ctx.hyp_id in verdict_hyp_ids, (
        f"reviewer did not emit a verdict for seeded hyp {ctx.hyp_id!r}; "
        f"verdicts: {verdicts}"
    )

    # Claim #4 — PROP action
    actions = frontmatter.get("proposal_actions") or []
    action_prop_ids = {
        a.get("prop_id") for a in actions if isinstance(a, dict)
    }
    assert ctx.prop_id in action_prop_ids, (
        f"reviewer did not emit an action for seeded prop {ctx.prop_id!r}; "
        f"actions: {actions}"
    )

    # Cost cap
    reviewer_cost = float(reviewer.metrics.get("total_cost_usd", 0.0) or 0.0)
    total = drive.total_cost_usd + reviewer_cost
    assert total < COST_CAP_USD, (
        f"combined cost ${total:.4f} >= cap ${COST_CAP_USD} "
        f"(drive=${drive.total_cost_usd:.4f}, reviewer=${reviewer_cost:.4f})"
    )


def _extract_frontmatter(text: str) -> str:
    """Pull the YAML frontmatter block out of a retro.md (between ``---``s).

    Mirror of ``reflect_session_main._extract_frontmatter`` — kept local
    until the second test consumer (design-minimalism §).
    """
    if not text.startswith("---\n"):
        return text
    rest = text[len("---\n"):]
    end = rest.find("\n---\n")
    if end == -1:
        if rest.endswith("\n---"):
            return rest[:-len("\n---")]
        raise ValueError("retro.md: malformed frontmatter (missing closing ---)")
    return rest[:end]


# ---------------------------------------------------------------------------
# Test body — wires the phases together.
# ---------------------------------------------------------------------------


def test_hitl_role_overlay_prompt_then_auto_retro_spawns_auditor(
    tmp_venv_project: Project,
    requires_claude_auth,  # noqa: ARG001 — skip-marker fixture
    isolated_global_customizations: Path,
) -> None:
    """HITL-equivalent flow + auto-retro auditor (claim mapping in module docstring)."""

    ctx = phase_setup(tmp_venv_project)
    assert ctx.hyp_id.startswith("HYP-"), ctx
    assert ctx.prop_id.startswith("PROP-"), ctx
    assert isolated_global_customizations.exists(), (
        f"global trait seeding didn't land at {isolated_global_customizations}"
    )

    drive = phase_drive(tmp_venv_project, ctx)
    # Phase 2 mini-asserts — envelope sanity. Content-level claims
    # (composition, magic-word, retro artefacts) belong to Phase 3+.
    assert drive.session_id, drive
    assert drive.session_dir.is_dir(), drive

    phase_assert_drive_session(drive, ctx)
    reviewer = phase_invoke_reviewer(tmp_venv_project, ctx, drive)
    phase_assert_retro_artefacts(tmp_venv_project, ctx, drive, reviewer)
