"""Session-identity env contract: one key set, one home, sanctioned mirrors only (HATS-1613).

The contract itself is ADR-0025; this is its guard. Five invariants:

A. each contract key is *defined* exactly once inside the integrator;
B. ``ai_hats.env`` exposes all of them (re-export is fine — one import surface);
C. the mirrors that may not import the home (rack) carry the same spellings;
D. ``ENV_TASKS_DIR`` in the home is NOT rack's ``RACK_TASKS_DIR`` — a near-name
   whose accidental unification would splice two unrelated contracts;
E. the identity is REMOVED exactly as it is written — a subset tears it, and the
   three sites that hand-rolled their own key list each tore it differently.
"""  # comment-length: allow — the invariant set is the contract

from __future__ import annotations

import ast
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
