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
        # `ai-hats task` claim/refuse non-deterministically in e2e subprocesses.
        # Tests that exercise ownership set them explicitly after copying env.
        "AI_HATS_SESSION_ID",
        "AI_HATS_ROOT_PID",
        "AI_HATS_INIT_UPDATED",
    }
)


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
    return {k: v for k, v in src.items() if k not in ENV_DENYLIST}


def launcher_subprocess_env(
    base: Mapping[str, str],
    *,
    repo_url: str | os.PathLike[str],
    venv: str | os.PathLike[str],
    user_home: str | os.PathLike[str],
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
    """
    env = clean_env(base)
    env[ENV_REPO_URL] = str(repo_url)
    env[ENV_AI_HATS_VENV] = str(venv)
    env["AI_HATS_USER_HOME"] = str(Path(user_home))
    env.pop(ENV_LAUNCHER_DEST, None)
    # HATS-1019: merge is default-deny; the e2e inventory tests merge
    # semantics, not consent. Gate tests pop this from a copied env.
    env.setdefault("AI_HATS_MERGE_ACK", "1")
    return env
