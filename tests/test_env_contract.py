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
# candidate), so ADR-0023 binds them to "reconcile behaviour, not remove
# duplication" — conformance-checked here rather than deduplicated.
SANCTIONED_MIRRORS = {
    "ai_hats_rack.journal": ("ENV_SESSION_ID", "ENV_ROOT_PID"),
    "ai_hats_rack.cli_common": ("ENV_SESSION_ID",),
    "ai_hats_rack.resolver": ("ENV_AI_HATS_DIR", "ENV_AI_HATS_PROJECT_DIR"),
    "ai_hats_observe.trace": ("ENV_SESSION_ID",),
    # Runs on every agy tool call and must not import ai-hats (its own module
    # docstring) — a mirror for availability, not for taste.
    "ai_hats_agy.hook_dispatcher": (
        "ENV_SESSION_ID",
        "ENV_AI_HATS_PROJECT_DIR",
        "ENV_SESSION_CACHE_DIR",
        # It reads the envelope whole (json.loads, no ai-hats import) rather than
        # reassembling the session from scalars that may not belong together.
        "ENV_SESSION_IDENTITY",
    ),
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
NON_CONTRACT_HOOK_KEYS = {
    # Point-specific vocabulary of the caller: composition of the channel.
    "AI_HATS_BRANCH_NAME",
    "AI_HATS_BYPASS_JOURNAL",
    "AI_HATS_HOOK_EVENT",
    # Kill switches and acknowledgements: a human standing one gate down.
    "AI_HATS_BACKLOG_GATE_OFF",
    "AI_HATS_COMMENT_LINT_OFF",
    "AI_HATS_DESTRUCTIVE_ACK",
    "AI_HATS_DOCS_INDEX_ACK",
    # Read by the rack process; NAMED in safety_gate because it refuses them as an
    # inline self-grant rather than reading them (HATS-1639). The consent ticket
    # is minted by safety_gate and spent by the rack's plan gate (HATS-1642) — a
    # per-transition nonce, not a flag a human ever sets.
    "AI_HATS_CONSENT_TICKET",
    "AI_HATS_MERGE_ACK",
    "AI_HATS_PLAN_ACK",
    "AI_HATS_NO_RAW_DESTRUCTIVE_SKIP",
    "AI_HATS_PRIVACY_ACK",
    "AI_HATS_RULE_DELIVERY_ACK",
    "AI_HATS_SECURITY_LINT_OFF",
    "AI_HATS_SHARED_STATE_ACK",
    "AI_HATS_SKILL_LINT_ACK",
    "AI_HATS_SMOKE_SKIP",
    "AI_HATS_TOOL_HYGIENE_OFF",
    "AI_HATS_WT_ENTRY_OFF",
    "AI_HATS_WT_GATE_OFF",
    "AI_HATS_YOLO",
    # Tuning knobs and config overrides: "how much" / "run what", set by a human.
    "AI_HATS_COMMENT_MAX_LINES",
    "AI_HATS_DOCSTRING_MAX_CHARS",
    "AI_HATS_DOCSTRING_MAX_LINES",
    "AI_HATS_E2E_CLEAN_TMP",
    "AI_HATS_E2E_REQUIRE_VENV",
    "AI_HATS_RULE_DELIVERY_CMD",
    "AI_HATS_SKILL_LINT_CMD",
    "AI_HATS_WT_GATE_EXTS",
}


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
            f"(ADR-0023: reconcile behaviour, not remove duplication)"
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
    unread = sorted(NON_CONTRACT_HOOK_KEYS - set(found))
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

    from ai_hats.session_artifacts import assemble_launch_env
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
    path, in the other direction. Conformance rather than dedup (ADR-0023).
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
