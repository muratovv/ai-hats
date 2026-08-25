"""Session-identity env contract: one key set, one home, sanctioned mirrors only (HATS-1613).

The contract itself is ADR-0025; this is its guard. Six invariants:

A. each contract key is *defined* exactly once inside the integrator;
B. ``ai_hats.env`` exposes all of them (re-export is fine — one import surface);
C. the mirrors that may not import the home (rack) carry the same spellings;
D. ``ENV_TASKS_DIR`` in the home is NOT rack's ``RACK_TASKS_DIR`` — a near-name
   whose accidental unification would splice two unrelated contracts;
E. the identity is REMOVED exactly as it is written — a subset tears it, and the
   three sites that hand-rolled their own key list each tore it differently;
F. the hook scripts shipped into user projects spell every ``AI_HATS_*`` they
   read either as a contract key or as a name this file admits is not one.
"""  # comment-length: allow — the invariant set is the contract

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from ai_hats.constants import (
    BYPASS_FLAGS_NOT_INHERITED,
    CONSENT_OWNED_KEYS,
    withheld_from_subagent,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATOR_SRC = REPO_ROOT / "src" / "ai_hats"
HOME_MODULE = INTEGRATOR_SRC / "env.py"

# The 13 keys of the contract (ADR-0025 + ADR-0020 D2), as
# ``expected attribute name on ai_hats.env`` -> ``env var spelling``.
IDENTITY_KEYS = {
    "ENV_SESSION_ID": "AI_HATS_SESSION_ID",
    "AI_HATS_PROJECT_DIR_ENV": "AI_HATS_PROJECT_DIR",
    "ENV_AI_HATS_DIR": "AI_HATS_DIR",
    "ENV_AI_HATS_VENV": "AI_HATS_VENV",
    "ENV_SESSION_CACHE_DIR": "AI_HATS_SESSION_CACHE_DIR",
    "ENV_ROLE": "AI_HATS_ROLE",
    "ENV_ROOT_PID": "AI_HATS_ROOT_PID",
    # A second interpreter pin: the agy global hook is invoked by the surface,
    # not by our launcher, so it cannot read AI_HATS_VENV's resolution.
    "ENV_AI_HATS_PYTHON": "AI_HATS_PYTHON",
    # Homed in observe beside the id it travels with, not in the env leaf.
    "ENV_TRACE_LOG_PATH": "TRACE_LOG_PATH",
    # The envelope (HATS-1594). Carrier of part of the set, so it is IN the set.
    "ENV_SESSION_IDENTITY": "AI_HATS_SESSION_IDENTITY",
}
HOOK_POINT_KEYS = {
    "ENV_HOOK_POINT": "AI_HATS_HOOK_POINT",
    "ENV_IN_HOOK": "AI_HATS_IN_HOOK",
    "ENV_TASK_ID": "AI_HATS_TASK_ID",
    "ENV_WORKTREE_PATH": "AI_HATS_WORKTREE_PATH",
    "ENV_TASKS_DIR": "AI_HATS_TASKS_DIR",
    "ENV_FORCE": "AI_HATS_FORCE",
}
CONTRACT_KEYS = {**IDENTITY_KEYS, **HOOK_POINT_KEYS}

# Mirrors that may NOT import the home (rack's import-hygiene pin forbids every
# candidate), so ADR-0026 binds them to "reconcile behaviour, not remove
# duplication" — conformance-checked here rather than deduplicated.
SANCTIONED_MIRRORS = {
    "ai_hats_rack.journal": ("ENV_SESSION_ID", "ENV_ROOT_PID"),
    "ai_hats_rack.cli_common": ("ENV_SESSION_ID",),
    "ai_hats_rack.resolver": ("ENV_AI_HATS_DIR", "ENV_AI_HATS_PROJECT_DIR"),
    "ai_hats_observe.trace": ("ENV_SESSION_ID",),
    # The agy dispatcher was the fifth entry until HATS-1826 folded the surface into
    # the integrator: `python -m ai_hats.surfaces.agy.hook_dispatcher` imports ai-hats
    # to reach itself, so "may not import the home" stopped being true and the four
    # spellings it mirrored now come from their homes.
}
# Spelling each mirror attribute must carry. Derived from the contract, plus the
# aliases where a mirror spells the *Python* name differently from the home.
MIRROR_SPELLINGS = {
    **CONTRACT_KEYS,
    "ENV_AI_HATS_PROJECT_DIR": "AI_HATS_PROJECT_DIR",
}

# comment-length: allow — why the shipped hooks get their own regime.
# Hook scripts are DATA materialised into arbitrary user projects and run by the
# SYSTEM interpreter, where ``ai_hats`` may not be importable at all (and the
# library declares zero dependencies — packages/ai-hats-library/tests/
# test_library_boundary.py). So they cannot import the home, and they do not even
# name a constant SANCTIONED_MIRRORS could resolve: the spellings are inline
# literals. Same regime as C, one level lower — conformance, not dedup.
SHIPPED_HOOK_ROOT = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
# ``git_hooks`` is in scope with ``hooks``: the split is where the file gets
# installed (.git/hooks vs the surface's hook config), not what it may read.
# ``lib`` joined them in HATS-1614: a hook's shared body reads the environment
# on the hook's behalf, so leaving it out let a name walk out of this scan just
# by being factored out of the script that used to spell it — which is how this
# very test caught the gate primitive's move.
SHIPPED_HOOK_DIRS = {"hooks", "git_hooks", "lib"}
_ENV_NAME = re.compile(r"AI_HATS_[A-Z0-9_]+")

# comment-length: allow — the border this table draws IS the invariant.
# The AI_HATS_* names a shipped hook legitimately reads that are NOT contract
# keys. ADR-0025 D1 draws this border itself — the caller's point-specific
# vocabulary and the tuning knobs are outside the identity set by construction —
# and names it explicitly because "an unnamed neighbour is the mechanism by which
# the set later drifts". Enumerating them here is what closes the vocabulary, and
# a closed vocabulary is the only thing that can tell a typo from a new key.
# The kill switches a sub-agent must NOT inherit live in production — it is the
# side that acts on them (`constants.BYPASS_FLAGS_NOT_INHERITED`, HATS-1743). The
# three groups below are what remains: names a shipped hook reads that are neither
# contract keys nor withheld approvals.

# Point-specific vocabulary of the caller: composition of the channel.
POINT_SPECIFIC_HOOK_KEYS = {
    "AI_HATS_BRANCH_NAME",
    "AI_HATS_BYPASS_JOURNAL",
    "AI_HATS_HOOK_EVENT",
}


# Tuning knobs and config overrides: "how much" / "run what", set by a human. A knob
# is not an approval, so withholding one from a child would change behaviour rather
# than withhold consent.
TUNING_KNOB_KEYS = {
    "AI_HATS_COMMENT_MAX_LINES",
    "AI_HATS_DOCSTRING_MAX_CHARS",
    "AI_HATS_DOCSTRING_MAX_LINES",
    "AI_HATS_E2E_CLEAN_TMP",
    "AI_HATS_E2E_REQUIRE_VENV",
    "AI_HATS_GATE_MARKER_KEEP_DAYS",
    "AI_HATS_RULE_DELIVERY_CMD",
    "AI_HATS_SKILL_LINT_CMD",
    "AI_HATS_WT_GATE_EXTS",
}

NON_CONTRACT_HOOK_KEYS = (
    POINT_SPECIFIC_HOOK_KEYS | CONSENT_OWNED_KEYS | TUNING_KNOB_KEYS | BYPASS_FLAGS_NOT_INHERITED
)


def _integrator_definitions() -> dict[str, list[str]]:
    """``env spelling -> ["relpath:line", ...]`` for every definition in the integrator."""
    sites: dict[str, list[str]] = {spelling: [] for spelling in CONTRACT_KEYS.values()}
    for py_file in sorted(INTEGRATOR_SRC.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = node.value
                targets = [node.target]
            else:
                continue
            if not isinstance(value, ast.Constant) or value.value not in sites:
                continue
            if any(isinstance(t, ast.Name) for t in targets):
                rel = py_file.relative_to(REPO_ROOT)
                sites[value.value].append(f"{rel}:{node.lineno}")
    return sites


# Declared home per key (ADR-0025 D1). SESSION_ID is observe's by HATS-948 and
# `env` cannot re-export it: the leaf-purity exemption runs one way only.
HOMES = {spelling: "ai_hats.env" for spelling in CONTRACT_KEYS.values()}
HOMES["AI_HATS_SESSION_ID"] = "ai_hats_observe.trace"
HOMES["TRACE_LOG_PATH"] = "ai_hats_observe.trace"
HOMES["AI_HATS_SESSION_IDENTITY"] = "ai_hats.session_identity"


@pytest.mark.parametrize(("attr", "spelling"), sorted(CONTRACT_KEYS.items()))
def test_contract_key_is_exposed_by_its_declared_home(attr: str, spelling: str) -> None:
    """B — every contract key is reachable from its one declared home (ADR-0025 D1)."""
    import importlib

    module = importlib.import_module(HOMES[spelling])
    assert hasattr(module, attr), (
        f"{attr} ({spelling}) is not exposed by {HOMES[spelling]} — its declared "
        f"home (ADR-0025 D1). Six hook-point keys are raw literals in "
        f"src/ai_hats/hook_exec.py today."
    )
    assert getattr(module, attr) == spelling


def test_each_contract_key_is_defined_exactly_once_in_the_integrator() -> None:
    """A — no second declaration of a key the home already owns."""
    duplicates = {
        spelling: sites for spelling, sites in _integrator_definitions().items() if len(sites) > 1
    }
    assert not duplicates, (
        "contract keys declared more than once inside the integrator — the home "
        "(src/ai_hats/env.py) must be the only definition, others re-export:\n"
        + "\n".join(f"  {k}: {', '.join(v)}" for k, v in sorted(duplicates.items()))
    )


@pytest.mark.parametrize("module_name", sorted(SANCTIONED_MIRRORS))
def test_sanctioned_mirror_spellings_match_the_home(module_name: str) -> None:
    """C — a mirror that cannot import the home still agrees with it."""
    import importlib

    module = importlib.import_module(module_name)
    for attr in SANCTIONED_MIRRORS[module_name]:
        assert hasattr(module, attr), f"{module_name}.{attr} vanished — mirror drifted"
        assert getattr(module, attr) == MIRROR_SPELLINGS[attr], (
            f"{module_name}.{attr} drifted from the home spelling "
            f"{MIRROR_SPELLINGS[attr]!r}; rack may not import the home "
            f"(ADR-0026: reconcile behaviour, not remove duplication)"
        )


def _shipped_hook_env_literals() -> dict[str, list[str]]:
    """``AI_HATS_* name -> ["relpath:line", ...]`` across every shipped hook script.

    ``.py`` is read with ``ast`` and only WHOLE string constants count, because
    only those are env reads: ``safety_gate.py:31`` names two ACKs in a comment
    and ``:178`` holds the prefix ``"AI_HATS_YOLO="``, none of which is a key.
    Everything else (shell, json) has no cheap AST and is scanned line-wise.
    The five vendored ``bypass_journal.py`` are symlinks, so resolving collapses
    them onto the one file a fix must actually edit.
    """
    found: dict[str, set[str]] = {}
    seen: set[Path] = set()
    for path in sorted(SHIPPED_HOOK_ROOT.rglob("*")):
        parents = set(path.relative_to(SHIPPED_HOOK_ROOT).parts[:-1])
        if not path.is_file() or not (SHIPPED_HOOK_DIRS & parents):
            continue
        real = path.resolve()
        if real in seen:
            continue
        seen.add(real)
        try:
            text = real.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = real.relative_to(REPO_ROOT)
        hits: list[tuple[str, int]] = []
        if real.suffix == ".py":
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if _ENV_NAME.fullmatch(node.value):
                        hits.append((node.value, node.lineno))
        else:
            for lineno, line in enumerate(text.splitlines(), 1):
                hits.extend((name, lineno) for name in _ENV_NAME.findall(line))
        for name, lineno in hits:
            found.setdefault(name, set()).add(f"{rel}:{lineno}")
    return {name: sorted(sites) for name, sites in found.items()}


def test_shipped_hook_scripts_spell_only_names_the_contract_knows() -> None:
    """F — an AI_HATS_* literal in a shipped hook is a contract key or a named non-key.

    These scripts run where nothing can check them: no import of the home, no
    constant to resolve, no failure when the read misses — a typo just yields the
    default forever. Closing the vocabulary is what turns that into a test.
    """
    found = _shipped_hook_env_literals()
    known = set(CONTRACT_KEYS.values()) | NON_CONTRACT_HOOK_KEYS
    unknown = {name: sites for name, sites in found.items() if name not in known}
    assert not unknown, (
        "shipped hook scripts read AI_HATS_* names the contract does not declare:\n"
        + "\n".join(f"  {name}  <- {', '.join(sites)}" for name, sites in sorted(unknown.items()))
        + "\nFix: if it is meant to be a contract key, spell it exactly as its home "
        "does (CONTRACT_KEYS at the top of this file, ADR-0025 D1) — the hook cannot "
        "import the home, so this test is the only thing that would ever notice. "
        "If it is a new kill switch or knob, add it to NON_CONTRACT_HOOK_KEYS."
    )

    # Doubles as the liveness pin: a scan that stops seeing files passes silently,
    # but every allowlisted name goes unread at once.
    # The withheld roster is pinned by its OWN universe below — gates live in repo
    # scripts too, and pinning it here is what left `AI_HATS_E2E_CATALOG_ACK` off it.
    unread = sorted(NON_CONTRACT_HOOK_KEYS - BYPASS_FLAGS_NOT_INHERITED - set(found))
    assert not unread, (
        f"NON_CONTRACT_HOOK_KEYS lists {unread}, which no shipped hook reads any "
        f"more. Drop them — an allowlist nobody exercises is a rubber stamp — "
        f"unless the whole list is here, in which case the hook layout moved and "
        f"SHIPPED_HOOK_ROOT / SHIPPED_HOOK_DIRS need re-pointing."
    )


def test_tasks_dir_near_name_is_not_the_racks_own_variable() -> None:
    """D — ``ENV_TASKS_DIR`` names two different contracts; never unify by symbol."""
    from ai_hats_rack import cli_common

    from ai_hats import env

    assert cli_common.ENV_TASKS_DIR == "RACK_TASKS_DIR"
    assert env.ENV_TASKS_DIR == "AI_HATS_TASKS_DIR"
    assert env.ENV_TASKS_DIR != cli_common.ENV_TASKS_DIR, (
        "the hook-point AI_HATS_TASKS_DIR and the rack CLI's own RACK_TASKS_DIR "
        "share a Python symbol name but are different contracts"
    )


class _Surface:
    """Only what ``assemble_launch_env`` touches, but spelling the pin like a real
    provider does (`surfaces/claude/provider.py:get_env`)."""

    name = "stub"

    def session_skills_root(self, project_dir, session_id):
        del project_dir, session_id
        return None

    def get_env(self, session_dir, project_dir):
        del session_dir
        from ai_hats.env import AI_HATS_PROJECT_DIR_ENV

        return {AI_HATS_PROJECT_DIR_ENV: str(project_dir)}

    def claim_launch_env(self, session_dir, project_dir):
        del session_dir, project_dir
        return {}


def test_the_envelope_and_its_scalars_agree_in_one_launch_env(tmp_path):
    """The envelope (HATS-1594) restates three values the scalars also carry.

    They agree today because ``assemble_launch_env`` is one composition root and
    builds both from the same arguments — but nothing said so, and a second
    writer is how every divergence in ADR-0025's context table began.
    """
    import json

    from ai_hats.session_artifacts import RunMode, assemble_launch_env
    from ai_hats.session_identity import ENV_SESSION_IDENTITY

    env = assemble_launch_env(
        _Surface(),
        tmp_path,
        tmp_path / "session",
        session_id="20260812-101500-3-4242",
        trace_path=str(tmp_path / "trace.log"),
        role="maintainer",
        root_pid="4242",
        extra_env={},
        run_mode=RunMode.HITL,
    )
    envelope = json.loads(env[ENV_SESSION_IDENTITY])

    from ai_hats.env import AI_HATS_PROJECT_DIR_ENV

    assert envelope["project_dir"] == env[AI_HATS_PROJECT_DIR_ENV]
    assert envelope["id"] == env["AI_HATS_SESSION_ID"]
    assert envelope["role"] == env["AI_HATS_ROLE"]


# ---- E. removed exactly as written ----


def test_the_identity_is_removed_exactly_as_it_is_written(tmp_path):
    """``drop_identity`` and ``to_env`` must name the same key set.

    A remover that knows a SUBSET leaves a half-session behind, which ``from_env``
    refuses rather than reads as absence. Three sites hand-rolled this list and
    each got a different subset — the pin is what stops the fourth.
    """
    from ai_hats.session_identity import IDENTITY_ENV_KEYS, SessionIdentity, drop_identity

    written = SessionIdentity(
        id="20260812-101500-3-4242",
        role="maintainer",
        provider="claude",
        project_dir=tmp_path,
        session_dir=tmp_path / "session",
    ).to_env()

    assert set(IDENTITY_ENV_KEYS) == set(written), (
        "the drop list and the write list disagree — one of them is the tear"
    )

    env = {**written, "UNRELATED": "kept"}
    removed = drop_identity(env)

    assert env == {"UNRELATED": "kept"}, "the whole identity leaves, and nothing else does"
    assert set(removed) == set(written)
    assert SessionIdentity.from_env(env) is None, "what is left must read as 'no session'"


def test_the_sandbox_scrub_drops_the_whole_identity_too() -> None:
    """The experiments sandbox is bash, so it cannot call ``drop_identity``.

    It scrubbed five scalars and left the envelope, so a sandboxed run inherited
    the parent session while its scalars were gone — the same tear as the git
    path, in the other direction. Conformance rather than dedup (ADR-0026).
    """
    import re

    from ai_hats.session_identity import IDENTITY_ENV_KEYS

    scrub = (REPO_ROOT / "experiments" / "_lib" / "common.sh").read_text(encoding="utf-8")
    block = scrub.split("SCRUB=(", 1)[1].split(")", 1)[0]
    unset = set(re.findall(r"-u\s+(\w+)", block))

    missing = sorted(set(IDENTITY_ENV_KEYS) - unset)
    assert not missing, (
        f"the sandbox scrub leaves {missing} behind — the identity is torn, not removed"
    )


# ---- G. stood in for exactly as it is written ----


def _plants_a_bare_session_id(source: str) -> bool:
    """Does this module assign ``AI_HATS_SESSION_ID`` a value?

    By assignment, not by mention: a file that pops the key, reads it, or lists
    it to scrub is not standing in for a session, and a substring search cannot
    tell those apart. Two forms plant — a dict literal entry and a subscript
    assignment; the rest are readers.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "AI_HATS_SESSION_ID":
                    return True
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "AI_HATS_SESSION_ID"
            ):
                return True
    return False


def test_a_sandbox_standing_in_for_a_session_plants_the_whole_envelope() -> None:
    """Invariant E, read the other way round: written exactly as it is read.

    HATS-1594 made the envelope the only thing that counts as a session, because
    in production ``assemble_launch_env`` is its sole writer and always emits
    both keys — so a bare id can only come from an older build, and every reader
    now refuses it. Five e2e sandboxes kept planting the id alone and were
    refused for exactly the right reason, asserting nothing for 89 commits while
    reading as coverage (HATS-1644).

    File-scoped on purpose: proving the same dict receives both keys needs
    dataflow this cannot afford, and the incident class is a file whose readers
    migrated while its planting did not — which a file-scoped check does catch.
    """  # comment-length: allow — why a bare id stopped being a session
    offenders = []
    for path in sorted((REPO_ROOT / "tests" / "e2e").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not _plants_a_bare_session_id(source):
            continue
        if "AI_HATS_SESSION_IDENTITY" in source or "stand_in_session" in source:
            continue
        offenders.append(path.relative_to(REPO_ROOT / "tests" / "e2e").as_posix())

    assert not offenders, (
        f"{offenders} plant a bare AI_HATS_SESSION_ID — a session no launch produces, "
        f"which every reader refuses. Stand in for one with "
        f"`_helpers.sessions.stand_in_session` instead."
    )


def test_the_flags_a_sub_agent_never_inherits_are_productions_to_name() -> None:
    """G — the neutralised set lives in production, and this file derives from it.

    The vocabulary lived only here, so nothing could ACT on it: production cannot
    import a test. A supervisor's approval is scoped to a session
    (`rule_pause_before_shared_state_write` §41), and a sub-agent is a different
    one — HATS-1743.
    """
    assert BYPASS_FLAGS_NOT_INHERITED <= NON_CONTRACT_HOOK_KEYS
    # Consent keeps its own artefacts and its own cards (HATS-1738 / HATS-1739):
    # whether a grant crosses into a child is the engine's to answer, not this seam's.
    assert not (BYPASS_FLAGS_NOT_INHERITED & CONSENT_OWNED_KEYS)
    # A knob answers "how much", never "may I" — withholding one changes behaviour
    # instead of withholding an approval.
    assert not (BYPASS_FLAGS_NOT_INHERITED & TUNING_KNOB_KEYS)


#: Where a gate that reads an approval can live. Wider than the shipped-hook scan on
#: purpose: `scripts/` holds gates too, and trusting the narrower universe is exactly
#: what left a live flag off the roster (HATS-1743 review).
def _production_bypass_literals() -> set:
    """Every withheld-shaped ``AI_HATS_*`` an executable under src/scripts/packages reads."""
    # HATS-1826 folded packages/surfaces/*/src into src/ai_hats/surfaces, which the
    # first root already walks — so no second, deeper glob is needed any more.
    roots = [REPO_ROOT / "src", REPO_ROOT / "scripts", *sorted(REPO_ROOT.glob("packages/*/src"))]
    # The module that DECLARES the roster is not a reader of it; scanning it would
    # make this test agree with itself.
    declaring = REPO_ROOT / "src" / "ai_hats" / "constants.py"
    spelling = re.compile(r"\bAI_HATS_[A-Z0-9_]+")
    found = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in (".py", ".sh") or not path.is_file() or path == declaring:
                continue
            # An area's tests live inside its own folder (ADR-0026 D5, HATS-1826); a
            # fixture is not a gate, and the wheel does not ship them either.
            if "tests" in path.relative_to(root).parts:
                continue
            found |= {
                name
                for name in spelling.findall(path.read_text(encoding="utf-8", errors="ignore"))
                if withheld_from_subagent(name)
            }
    return found


def test_every_withheld_name_answers_the_shape_test_that_holds_the_line() -> None:
    """G1 — the roster and the predicate cannot disagree about what a bypass is.

    The roster is a convenience over the predicate, never a second opinion: if a name
    on it failed the shape test, the launch record would promise a withholding the
    live environment would not perform.
    """
    disagree = sorted(n for n in BYPASS_FLAGS_NOT_INHERITED if not withheld_from_subagent(n))
    assert not disagree, f"on the roster but not withheld-shaped: {disagree}"


def test_the_roster_names_every_approval_production_actually_reads() -> None:
    """G2 — completeness over the universe where gates really live.

    A miss here costs only a line in the launch record — the shape test still
    withholds the flag — but the roster is what a reader trusts, so it is pinned.
    """
    read = _production_bypass_literals()
    missing = sorted(read - BYPASS_FLAGS_NOT_INHERITED - CONSENT_OWNED_KEYS)
    assert not missing, (
        f"production reads withheld-shaped names the roster does not list: {missing}. "
        "Add them to BYPASS_FLAGS_NOT_INHERITED (they are already withheld at run "
        "time by shape — this keeps the launch record naming them)."
    )
    unread = sorted(BYPASS_FLAGS_NOT_INHERITED - read)
    assert not unread, (
        f"the roster lists {unread}, which nothing under src/scripts/packages reads "
        "any more — drop them, or the roster becomes a rubber stamp."
    )
