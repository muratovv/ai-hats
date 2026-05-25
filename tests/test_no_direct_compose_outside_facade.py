"""HATS-456 drift guard — pin the single-derivation-point invariant.

Asserts that the **with-overlays** ``composer.compose(...)`` form only
appears inside ``ai_hats/materialize.py`` (the facade) plus this test.
Every other "compose role X for this project" call must route through
``compose_for_role`` so the four real-runtime consumers (HITL runner,
sub-agent runner, on-disk Assembler writer, ``MaterializeSystemPrompt``
step) cannot drift from each other.

Allowed exception: the **no-overlay** form (``compose(role)`` without
``overlays=``) belongs to deliberately different semantics:

- ``cli/reflect.py`` — reflect a target role's *built-in* composition
  for inspection / debugging, intentionally excluding project /global
  overlay layering. The semantic difference is the whole point of the
  command and is documented at the call site.

The regex below only catches the ``overlays=`` form so deliberate
no-overlay calls don't trip the guard. **However** — the regex is a
*loose* guard: it doesn't catch drift introduced by a new file calling
``composer.compose(role)`` *intending* layered composition but
forgetting ``overlays=``. That class of drift slipped past this test
once (HATS-501: ``pipeline/steps/compose.py`` was calling
``composer.compose(role)`` without overlays for a production funnel
value and the doc here previously whitelisted it as "audit-only"; it
isn't, and the step now routes through ``compose_for_role`` like every
other consumer). Strengthening the regex to flag direct
``composer.compose(role)`` calls inside ``pipeline/`` is tracked in
HATS-505.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src" / "ai_hats"

# The compose-with-overlays signature is the canonical
# "compose role X for this project's overlay layering" call. Matches
# both ``composer.compose(role, overlays=...)`` and the
# ``self.composer.compose(role, overlays=...)`` spelling on the same
# line; line breaks inside the call are matched by reading the full
# file and checking with DOTALL.
COMPOSE_WITH_OVERLAYS_RE = re.compile(
    r"\bcomposer\.compose\([^)]*\boverlays\s*=",
    flags=re.DOTALL,
)

# Files that are allowed to mention the pattern verbatim.
ALLOWED_FILES = {
    SRC_DIR / "materialize.py",
}


def test_compose_with_overlays_only_in_facade():
    """The compose-with-overlays form must appear exactly once in
    src/ai_hats/, inside materialize.py. Any other hit is a drift
    signal — the file should route through ``compose_for_role``
    instead.
    """
    offenders: list[tuple[Path, str]] = []
    for py_file in SRC_DIR.rglob("*.py"):
        if py_file in ALLOWED_FILES:
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in COMPOSE_WITH_OVERLAYS_RE.finditer(text):
            # Surface a few characters of context for the failure message.
            start = max(0, match.start() - 20)
            end = min(len(text), match.end() + 40)
            snippet = text[start:end].replace("\n", " ")
            offenders.append((py_file.relative_to(REPO_ROOT), snippet))

    assert not offenders, (
        "HATS-456 drift: composer.compose(..., overlays=...) appears "
        "outside the materialize.py facade. Route through "
        "compose_for_role(assembler, role) instead.\n"
        + "\n".join(f"  {p}: …{snip}…" for p, snip in offenders)
    )


def test_facade_itself_contains_one_compose_call():
    """Sanity check: materialize.py must still contain exactly one
    ``composer.compose(`` call (the one inside ``compose_for_role``).
    Catches accidental loss of the implementation during refactors.
    """
    text = (SRC_DIR / "materialize.py").read_text(encoding="utf-8")
    # Strip docstrings to avoid counting the references the module's
    # docstring makes to the legacy spelling for documentation.
    # Simpler: just count "composer.compose(" code occurrences by
    # ignoring lines starting with a triple-quote / inside backticks
    # is overkill — count lines that look like code-not-docstring.
    code_calls = 0
    in_doc = False
    for raw in text.splitlines():
        stripped = raw.strip()
        # Toggle docstring/comment fences (very rough; the module uses
        # plain triple-double-quoted module docstring at top of file).
        if stripped.startswith('"""') or stripped.endswith('"""'):
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        if stripped.startswith("#"):
            continue
        # Skip lines that have the pattern inside backticks (rst markup).
        if "``" in stripped:
            continue
        if "composer.compose(" in stripped:
            code_calls += 1
    assert code_calls == 1, (
        f"expected exactly 1 composer.compose( call in materialize.py "
        f"(code, not docstring), got {code_calls}. text:\n{text}"
    )
