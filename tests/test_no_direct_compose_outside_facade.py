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

The first test below (overlays= form) only catches drift where a file
*meant* to compose with overlays but did so outside the facade.

The second test (HATS-505) closes the other half: any
``composer.compose(...)`` call inside ``src/ai_hats/pipeline/`` — with
or without ``overlays=`` — is a drift signal, because pipeline steps
deliver composition to live agents and MUST route through
``compose_for_role`` so the layered composition reaches the runner.
HATS-501 slipped past the original test precisely because the
no-overlay form wasn't flagged inside pipeline/.
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


# HATS-505: any ``composer.compose(...)`` call, regardless of whether
# ``overlays=`` is present, with or without ``self.`` qualifier.
ANY_COMPOSE_CALL_RE = re.compile(
    r"\bcomposer\.compose\s*\(",
)

# Whitelist for the pipeline-scoped guard. Files inside ``src/ai_hats/
# pipeline/`` are NOT allowed; the canonical role-delivery path is the
# ``compose_for_role`` facade. The only project-wide deliberate
# no-overlay site (``cli/reflect.py``) is OUTSIDE ``pipeline/`` and
# therefore not in scope here — its justification lives at the call.
NO_DIRECT_COMPOSE_IN_PIPELINE_ALLOWED: set[Path] = set()


def _strip_docstrings_and_comments(text: str) -> str:
    """Crude stripper: drop ``#``-comment-only lines and lines inside a
    triple-double-quoted block. Sufficient for this test's purpose
    (false-positive prevention on prose mentioning ``composer.compose``
    inside docstrings / RST backticks)."""
    out: list[str] = []
    in_doc = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith('"""') and not (
            stripped.endswith('"""') and len(stripped) > 3
        ):
            in_doc = not in_doc
            continue
        if stripped.startswith('"""') and stripped.endswith('"""'):
            # one-line docstring
            continue
        if in_doc:
            continue
        if stripped.startswith("#"):
            continue
        # Skip lines where the pattern appears only inside backticks
        # (rst / docstring references that survived the docstring strip
        # because they're at module-body level in code comments).
        if "``" in stripped and stripped.count("`") >= 4:
            # Likely an inline rst/backtick reference; drop the line.
            continue
        out.append(raw)
    return "\n".join(out)


def test_no_direct_compose_inside_pipeline_subtree():
    """HATS-505 drift guard: any ``composer.compose(...)`` call inside
    ``src/ai_hats/pipeline/`` MUST route through ``compose_for_role``
    (materialize.py facade). Direct calls bypass overlay layering and
    are the HATS-501 root-cause pattern.
    """
    pipeline_dir = SRC_DIR / "pipeline"
    assert pipeline_dir.is_dir(), pipeline_dir

    offenders: list[tuple[Path, str]] = []
    for py_file in pipeline_dir.rglob("*.py"):
        if py_file in NO_DIRECT_COMPOSE_IN_PIPELINE_ALLOWED:
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        cleaned = _strip_docstrings_and_comments(text)
        for match in ANY_COMPOSE_CALL_RE.finditer(cleaned):
            line_start = cleaned.rfind("\n", 0, match.start()) + 1
            line_end = cleaned.find("\n", match.end())
            line = cleaned[
                line_start: line_end if line_end > 0 else None
            ].strip()
            offenders.append((py_file.relative_to(REPO_ROOT), line))

    assert not offenders, (
        "HATS-505 drift: composer.compose(...) appears inside "
        "src/ai_hats/pipeline/ — pipeline steps must route through "
        "compose_for_role(assembler, role) (materialize.py facade) "
        "so layered composition (project + global overlays) is "
        "preserved. HATS-501 was caused by exactly this pattern.\n"
        + "\n".join(f"  {p}: {line}" for p, line in offenders)
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
