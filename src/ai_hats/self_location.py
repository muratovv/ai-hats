"""Runtime self-location guard (HATS-791, Alt 3 — refuse-and-instruct).

The "shadow" problem: a stale ``ai-hats`` installed into some *foreign*
(non-managed) virtualenv — typically a project's own app-venv into which a
user once ``pip install``ed ai-hats — gets reached ahead of the host launcher
and runs mis-resolved against the wrong project. HATS-790 (Alt 5) removed the
``[project.scripts] ai-hats`` console-script generator, so a managed venv no
longer materialises a shadowable ``bin/ai-hats`` proxy — that closed the
common direnv-prepend vector. This module is the **backstop** for the residual
case: someone running ``python -m ai_hats`` directly from a foreign venv.

Design: this is defense-in-depth, NOT a primary gate. The generator that
created shadows is already gone (HATS-790). A *missed* shadow merely reproduces
pre-HATS-791 behaviour; a *false-positive* would brick a perfectly good CLI.
So the policy biases HARD toward fail-open — :func:`classify_invocation` only
ever returns ``"foreign"`` when it is positively certain the running
interpreter is a real venv that is none of the sanctioned shapes. Every
ambiguity (no project, unresolved venv, resolution error) resolves to
``"sanctioned"``.

The decision is a PURE function so it is exhaustively truth-table testable
without spawning interpreters. The CLI wiring (compute ``resolved_venv`` /
``is_editable_install`` / ``skip``, then refuse-and-instruct on ``"foreign"``)
lives in :func:`ai_hats.cli.main_entry`, the real-invocation entry point — NOT
in the bare ``main`` click group that in-process ``CliRunner`` tests drive, so
the test suite bypasses the guard for free.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

#: Env var that disables the guard outright (escape hatch). When set to "1"
#: :func:`classify_invocation` always returns ``"sanctioned"``.
SKIP_ENV_VAR = "AI_HATS_SKIP_SELF_LOCATION_GUARD"

Verdict = Literal["sanctioned", "foreign"]


def _under_managed_namespace(prefix: Path) -> bool:
    """True iff ``prefix`` lives under a project's ``.agent/ai-hats/`` tree.

    Matches both the default ``<project>/.agent/ai-hats/.venv`` and the
    blue-green ``<project>/.agent/ai-hats/versions/<sha>/`` (HATS-647) layouts
    — any path whose ancestry contains the ``.agent/ai-hats`` segment pair is
    framework-managed and therefore sanctioned, regardless of which managed
    project owns it. We key on the directory-name pair rather than resolving a
    specific project so an interpreter from *some* managed install never trips
    the guard even when the resolver could not name the venv.
    """
    parts = prefix.parts
    seg = (".agent", "ai-hats")
    for i in range(len(parts) - 1):
        if parts[i] == seg[0] and parts[i + 1] == seg[1]:
            return True
    return False


def classify_invocation(
    running_prefix: str | os.PathLike[str] | None,
    resolved_venv: str | os.PathLike[str] | None,
    *,
    is_editable_install: bool,
    skip: bool,
) -> Verdict:
    """Classify the running interpreter as ``"sanctioned"`` or ``"foreign"``.

    ``running_prefix`` is the venv root the live interpreter belongs to
    (``sys.prefix``). ``resolved_venv`` is the venv ai-hats *would* resolve for
    the current project (``ai_hats.paths.venv_path`` / the launcher's
    ``AI_HATS_VENV`` pin), or ``None`` when no project / resolution failed.

    Contract (sanctioned ⇒ allow; the policy is FAIL-OPEN — only a positively
    identified foreign venv is refused):

      1. ``skip`` is True (env ``AI_HATS_SKIP_SELF_LOCATION_GUARD=1``)
         → ``"sanctioned"`` (operator escape hatch).
      2. ``is_editable_install`` is True (PEP 610 editable host clone,
         ``channel: local``) → ``"sanctioned"`` (dev checkout, never managed).
      3. ``running_prefix`` is unknown/empty → ``"sanctioned"`` (cannot judge).
      4. ``running_prefix`` lives under a ``<project>/.agent/ai-hats/`` tree
         (default ``.venv`` or ``versions/<sha>/``) → ``"sanctioned"``.
      5. ``resolved_venv`` is None / unknown → ``"sanctioned"`` (no project or
         a resolution error — nothing to compare against, so fail open).
      6. ``running_prefix == resolved_venv`` (same venv, path-normalised)
         → ``"sanctioned"`` (running exactly the venv we'd resolve).
      7. Otherwise the interpreter is a real venv that is none of the above
         → ``"foreign"``.

    Order matters: the cheap unconditional escape hatches (1, 2) come first so
    they hold even when path math would otherwise raise; (4) is checked before
    (5) so a managed interpreter is sanctioned even with an unresolved project.
    """
    # 1. Explicit operator escape hatch — unconditional.
    if skip:
        return "sanctioned"

    # 2. Editable host clone (dev checkout / channel:local) — never a managed
    #    venv, and bricking a developer's working tree is the worst outcome.
    if is_editable_install:
        return "sanctioned"

    # 3. Unknown running interpreter — cannot judge, fail open.
    if running_prefix is None or str(running_prefix) == "":
        return "sanctioned"

    run = _norm(running_prefix)
    if run is None:
        return "sanctioned"

    # 4. Managed-namespace interpreter (default .venv or versions/<sha>/).
    if _under_managed_namespace(run):
        return "sanctioned"

    # 5. No resolvable target venv (no project / resolution error) — fail open.
    if resolved_venv is None or str(resolved_venv) == "":
        return "sanctioned"

    resolved = _norm(resolved_venv)
    if resolved is None:
        return "sanctioned"

    # 6. Running exactly the venv we would resolve.
    if run == resolved:
        return "sanctioned"

    # 7. A real venv, none of the sanctioned shapes → the shadow case.
    return "foreign"


def _norm(p: str | os.PathLike[str]) -> Path | None:
    """Best-effort path normalisation for comparison.

    Expands ``~``, resolves symlinks where possible, and normalises ``..``.
    Returns ``None`` on any OS error so the caller fails open rather than
    raising out of a pure classifier. We use ``Path.resolve(strict=False)`` so
    a not-yet-created resolved venv (the launcher may resolve a path before the
    heal creates it) still normalises rather than raising.
    """
    try:
        return Path(p).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def remediation_text(running_prefix: str | os.PathLike[str] | None) -> str:
    """Human-readable refuse-and-instruct message for a foreign invocation.

    Names the three recovery paths the spec requires: run the host launcher,
    re-bootstrap out-of-band, or uninstall ai-hats from the offending venv.
    Also names the escape hatch so an operator who *intends* this can proceed.
    """
    where = str(running_prefix) if running_prefix else "(unknown)"
    return (
        "ai-hats: refusing to run from a foreign (non-managed) virtualenv.\n"
        f"  Running interpreter venv: {where}\n"
        "  This ai-hats was installed into a venv that is NOT the one ai-hats "
        "resolves for this project — it is a stale 'shadow' install and would "
        "run mis-resolved.\n"
        "\n"
        "  Fix it one of these ways:\n"
        "    1. Run the host launcher instead:\n"
        "         ~/.local/bin/ai-hats <command>\n"
        "    2. Re-bootstrap ai-hats (out-of-band recovery):\n"
        "         curl -LsSf https://github.com/muratovv/ai-hats/raw/master/scripts/bootstrap.sh | bash\n"
        "    3. Uninstall ai-hats from THIS venv:\n"
        f"         uv pip uninstall --python {where}/bin/python ai-hats\n"
        "\n"
        f"  To bypass this guard (advanced): set {SKIP_ENV_VAR}=1"
    )
