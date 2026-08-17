"""Subprocess env hygiene for e2e tests (HATS-685).

e2e install/launcher tests build a subprocess env and must exercise the REAL
installed ``ai_hats`` (and its ``ai_hats_library`` dependency), not the
developer's source tree. The trap: ``ai-hats wt exec`` sets a ``PYTHONPATH``
spanning the workspace source (``<repo>/src`` + each ``<repo>/packages/*/src``) —
the standard worktree test workaround. Inherited into a launcher subprocess, that
absolute ``PYTHONPATH`` shadows the installed packages with the source tree, so
the subprocess stops exercising the artefact under install. (Pre-HATS-876 the
library was force-included as ``ai_hats.library`` and absent from ``src``, so a
leak failed LOUD — ``files("ai_hats.library")`` → ``ModuleNotFoundError`` →
built-in roles vanished; post-HATS-876 ``ai_hats_library`` is a separate package
also on that ``PYTHONPATH``, so a leak quietly runs source-against-source.)

``ENV_DENYLIST`` is the set of python/ai_hats *redirect* vars that must never
leak into such a subprocess. The autouse fixture in ``conftest.py`` applies it
to ``os.environ`` for every e2e test, so any ``os.environ.copy()`` is clean by
construction. ``clean_env`` is the pure helper for call sites that prefer to be
explicit (and the unit-test surface).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR, ENV_AI_HATS_VENV
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.retired_dists import ENV_SKIP_PRUNE

# Redirect vars that must not leak into a real-install e2e subprocess. PYTHONPATH
# is the proven culprit (HATS-685); the rest are defensive siblings that could
# redirect the interpreter or ai_hats config the same way.
ENV_DENYLIST: frozenset[str] = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "VIRTUAL_ENV",
        ENV_AI_HATS_DIR,
        AI_HATS_PROJECT_DIR_ENV,
        "AI_HATS_USER_HOME",
        # HATS-887: session-scoped shared_launcher captures env before the
        # function-scoped GIT_* strip, so plumbing vars must be denied here too.
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        # HATS-955: task ownership keys off these; a leaked dev session would make
        # the backlog CLI claim/refuse non-deterministically in e2e subprocesses.
        # Tests that exercise ownership set them explicitly after copying env.
        "AI_HATS_SESSION_ID",
        "AI_HATS_ROOT_PID",
        # HATS-1280: tests/conftest.py sets this session-wide so a unit run never
        # uninstalls from the developer's venv. Inherited by an e2e subprocess it
        # would silently disable the very prune under test.
        ENV_SKIP_PRUNE,
        "XDG_CACHE_HOME",
        # HATS-1661: the pre-push gate exports it to parallelise its own run, but
        # a nested pytest that disables xdist (or lacks it) cannot parse `-n8`
        # and dies on argument parsing.
        "PYTEST_ADDOPTS",
    }
)
# HATS-1473: AI_HATS_CACHE_HOME is deliberately NOT denied — stripping it is what
# sent writes to the real cache. The repo-root conftest pins it session-wide, so
# inheriting it IS the isolation.


#: The point-agnostic env consent channel (HATS-1682). Named here so a grep for
#: it finds every scaffolding grant in one list.
CONSENT_ACK = "AI_HATS_CONSENT_ACK"


def consented(env: Mapping[str, str], **extra: str) -> dict[str, str]:
    """``env`` plus the consent grant, for ONE call that is SCAFFOLDING.

    Since HATS-1682 the role declares consent on `edge:review--done` (and
    `edge:plan--execute`), so a test that only needs a card *parked* in `done`
    now has to answer a question it is not measuring. This is the answer, and it
    is a function rather than a fixture or a conftest default on purpose:
    `test_consent_force_chain.py`, `test_wt_merge_consent_chain.py`,
    `test_wt_merge_consent_gate.py` and `test_rack_wiring.py` exist to prove the
    channel works, and an ambient grant would make all four vacuous — the exact
    defect class HATS-1682 was filed to remove. Grant it at the call, per call,
    and a reader can see which transition is scaffolding and which is the
    subject.

    Pure: never mutates ``env``.
    """  # comment-length: allow — why the grant is per-call and not ambient IS the contract
    return {**env, CONSENT_ACK: "1", **extra}


def consent_grant() -> dict[str, str]:
    """The grant alone, for a call site that takes an env OVERLAY rather than a
    whole env. Same rule as :func:`consented`: one call, never the suite."""
    return {CONSENT_ACK: "1"}


def checkout_pythonpath(repo_root: Path, existing: str = "") -> str:
    """PYTHONPATH that runs THIS checkout end-to-end (HATS-863).

    Delegates to the product contract (HATS-913) so test infra and
    ``ai-hats wt exec`` can never drift apart.
    """
    from ai_hats_wt import workspace_pythonpath

    return workspace_pythonpath(repo_root, existing)


def clean_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``base`` (default ``os.environ``) minus ``ENV_DENYLIST``.

    Pure: never mutates ``base``. Use when building a subprocess env that must
    run against the installed package rather than the source tree.
    """
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items() if k not in ENV_DENYLIST}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def launcher_subprocess_env(
    base: Mapping[str, str],
    *,
    repo_url: str | os.PathLike[str],
    venv: str | os.PathLike[str],
    user_home: str | os.PathLike[str],
    merge_ack: bool = False,
) -> dict[str, str]:
    """Build a hermetic env for a real-launcher e2e subprocess (HATS-828).

    The session-scoped ``shared_launcher`` fixture captures ``os.environ`` at
    SESSION setup — *before* the function-scoped autouse scrubs
    (``_scrub_redirect_env`` / ``_isolate_ai_hats_user_home``) apply — so it
    cannot rely on a pre-scrubbed ``os.environ``. It must isolate explicitly.
    This helper is that explicit transform, factored out so the regression test
    can apply the EXACT same logic to a deliberately-leaked base (deterministic
    fail-under-revert without drift).

    Two leaks are closed:

    * ``clean_env(base)`` drops ``ENV_DENYLIST`` — chiefly an **absolute**
      ``PYTHONPATH=<repo>/src`` (what ``ai-hats wt exec`` sets). Left in, it
      shadows the non-editable install's nested ``ai_hats.library`` → built-in
      roles vanish → "Role 'assistant' not found". (A *relative* ``src`` is
      harmless — it resolves against the subprocess cwd, not the repo.)
    * ``AI_HATS_USER_HOME`` is re-pinned to ``user_home`` (an empty dir).
      ``clean_env`` already strips the inherited value, but unset it falls back
      to the real ``HOME`` → the dev's ``~/.ai-hats/roles`` leak into
      composition. We pin ``AI_HATS_USER_HOME`` (NOT ``HOME``) deliberately:
      ``user_home()`` (``paths.py``) makes it the surgical knob that isolates
      only the ai-hats global slice, leaving ``HOME`` — and the warm
      ``~/.cache/uv`` + claude auth — intact (precedent:
      ``test_self_update_resilient_config.py``).

    ``AI_HATS_REPO_URL`` / ``AI_HATS_VENV`` pin the install source + shared venv;
    ``AI_HATS_LAUNCHER_DEST`` is dropped so a stray value can't redirect a child
    launcher install. Pure: never mutates ``base``.

    ``merge_ack`` is the ONLY source of ``AI_HATS_MERGE_ACK`` here and defaults
    to OFF (HATS-1682 T4). Pre-approving every merge is the exact environment the
    live probe named as the incident condition, so a caller that wants one says
    so; an inherited flag is dropped first, or a developer who exports one keeps
    the tier green where CI is red. Supersedes the HATS-1019 ``setdefault``.
    """
    env = clean_env(base)
    env[ENV_REPO_URL] = str(repo_url)
    env[ENV_AI_HATS_VENV] = str(venv)
    env["AI_HATS_USER_HOME"] = str(Path(user_home))
    env.pop(ENV_LAUNCHER_DEST, None)
    env.pop("AI_HATS_MERGE_ACK", None)
    if merge_ack:
        env["AI_HATS_MERGE_ACK"] = "1"
    return env
