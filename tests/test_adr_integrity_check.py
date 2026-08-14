"""HATS-1646 — the gate that refuses a citation into a decision record that is not there.

The fixtures below are the corpus's real declaration shapes, because the rule is
the whole difficulty: two obvious definitions were tried against the live corpus
and both failed — headers-only flagged the table and list markers, full-text went
green on ADR-0020's missing D5. Every shape here is one that exists in `docs/adr/`,
so a regression in the grammar fails HERE rather than silently in the scan.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# adr-integrity: fixtures — every citation below is test data aimed at a synthetic
# corpus, so the real check must not read this file as prose. It caught itself here.

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_adr_integrity.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_adr_integrity", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


def corpus(tmp_path: Path, adrs: dict[str, str], sources: dict[str, str] | None = None) -> Path:
    """A miniature repo: `docs/adr/<name>.md` plus citing files under `src/`."""
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    for name, body in adrs.items():
        (adr_dir / name).write_text(body, encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    for name, body in (sources or {}).items():
        (src / name).write_text(body, encoding="utf-8")
    return tmp_path


def dangling(root: Path) -> list[str]:
    found, _, _ = mod.scan(root)
    return [f"{d.number} {d.marker}".strip() for d in found]


# --- the five declaration shapes the corpus actually uses --------------------


@pytest.mark.parametrize(
    ("shape", "declaration"),
    [
        ("header", "### D1 — Таксономия каналов"),
        ("table row", "| D1 | in `merge()` | yes |"),
        ("list item", "- D1 — the extraction boundary"),
        ("bold lead", "**D1** extract `ai-hats-core` (HATS-862)"),
        ("blockquote table", "> | D1 god-`models` + kernel back-edges | split per domain |"),
    ],
)
def test_every_declaration_shape_resolves(tmp_path, shape, declaration):
    root = corpus(
        tmp_path,
        {"0001-a.md": f"# ADR-0001\n\n{declaration}\n"},
        {"m.py": '"""See ADR-0001 D1 for the rule."""\n'},
    )
    assert dangling(root) == [], f"{shape} must count as a declaration"


def test_bold_marker_counts_wherever_it_falls(tmp_path):
    """ADR-0014 lists `**T2**`…`**T15**` as one wrapped paragraph.

    Which of them opens a line is an artifact of where the prose wrapped, so a
    position-only rule flagged the legitimate `ADR-0014 T8` (found on the live
    corpus, not in review).
    """
    root = corpus(
        tmp_path,
        {
            "0001-a.md": "# ADR-0001\n\nPhase −1: **T7** generic migration (HATS-868), **T8**\nworkspace import lint.\n"
        },
        {"m.py": '"""See ADR-0001 T8."""\n'},
    )
    assert dangling(root) == []


# --- the rule's teeth: a mid-sentence mention is NOT a declaration -----------


def test_mid_sentence_mention_is_not_a_declaration(tmp_path):
    """The exact shape of the defect this check was written for.

    ADR-0020 mentions `D5` only while citing OTHER ADRs, and declares D1–D4. A
    full-text rule reports this corpus as clean — the false green that makes a
    check worse than no check.
    """
    root = corpus(
        tmp_path,
        {
            "0001-a.md": "# ADR-0001\n\n### D4 — the last one\n\nMechanical half of D4/D5, see [3] D5.\n"
        },
        {"m.py": '"""See ADR-0001 D5."""\n'},
    )
    assert dangling(root) == ["0001 D5"]


def test_marker_boundary_does_not_confuse_d1_with_d10(tmp_path):
    root = corpus(
        tmp_path,
        {"0001-a.md": "# ADR-0001\n\n### D10 — the tenth\n"},
        {"m.py": '"""See ADR-0001 D1."""\n'},
    )
    assert dangling(root) == ["0001 D1"]


# --- the three citation grammars the corpus actually uses --------------------


@pytest.mark.parametrize(
    ("grammar", "citation", "declaration"),
    [
        ("plain", "ADR-0001 D1", "### D1 — plain"),
        ("section sign", "ADR-0001 §D1", "### D1 — plain"),
        ("sub-index", "ADR-0001 P0 #4", "> | P0 #4 `subagent` mislabeled a brick |"),
        ("sub-index, no space", "ADR-0001 P0#4", "> | P0 #4 `subagent` mislabeled a brick |"),
        ("letter suffix", "ADR-0001 D4a", "### D4a — Явная зависимость"),
    ],
)
def test_every_citation_grammar_resolves(tmp_path, grammar, citation, declaration):
    root = corpus(
        tmp_path,
        {"0001-a.md": f"# ADR-0001\n\n{declaration}\n"},
        {"m.py": f'"""See {citation} for the rule."""\n'},
    )
    assert dangling(root) == [], f"grammar `{grammar}` must resolve"


def test_prose_section_name_is_not_treated_as_a_marker(tmp_path):
    """`ADR-0014 Composition rule` is a bare citation — declared uncovered, not flagged."""
    root = corpus(
        tmp_path,
        {"0001-a.md": "# ADR-0001\n\n### D1 — plain\n"},
        {"m.py": '"""The integrator seam (ADR-0001 Composition rule)."""\n'},
    )
    assert dangling(root) == []


# --- the second invariant: a number names exactly one file -------------------


def test_duplicate_number_is_reported(tmp_path):
    root = corpus(tmp_path, {"0001-a.md": "# ADR-0001\n", "0001-b.md": "# ADR-0001\n"})
    _, duplicates, _ = mod.scan(root)
    assert [d.number for d in duplicates] == ["0001"]
    assert duplicates[0].files == ("0001-a.md", "0001-b.md")


def test_a_marker_resolving_in_either_duplicate_still_reports_the_number(tmp_path):
    """The HATS-1641 false positive: D7 lived in the second file, the glob took the first."""
    root = corpus(
        tmp_path,
        {
            "0001-a.md": "# ADR-0001\n\n### D1 — one\n",
            "0001-b.md": "# ADR-0001\n\n### D7 — seven\n",
        },
        {"m.py": '"""See ADR-0001 D7."""\n'},
    )
    found, duplicates, _ = mod.scan(root)
    assert found == [], "the marker resolves in one of the two files"
    assert [d.number for d in duplicates] == ["0001"], "but the number is still ambiguous"


def test_unknown_number_is_reported(tmp_path):
    root = corpus(tmp_path, {"0001-a.md": "# ADR-0001\n"}, {"m.py": '"""See ADR-9999."""\n'})
    found, _, _ = mod.scan(root)
    assert [(f.number, f.marker) for f in found] == [("9999", "")]


# --- fixtures are not prose, and the skip is announced ----------------------


def test_opt_out_file_is_skipped_and_named(tmp_path):
    """This test file needs the escape itself: the check caught its own fixtures."""
    root = corpus(
        tmp_path,
        {"0001-a.md": "# ADR-0001\n\n### D1 — plain\n"},
        {"m.py": f'# {mod.OPT_OUT}\n"""See ADR-0001 D5, a citation that does not resolve."""\n'},
    )
    found, _, opted_out = mod.scan(root)
    assert found == []
    assert opted_out == ["src/m.py"], "an unannounced exclusion is the false green we refuse"


def test_a_mid_sentence_mention_does_not_opt_a_file_out(tmp_path):
    """CONTRIBUTING documents the sentinel, and thereby excluded its own citations."""
    root = corpus(
        tmp_path,
        {"0001-a.md": "# ADR-0001\n\n### D1 — plain\n"},
        {"m.py": f'"""A file opts out with `{mod.OPT_OUT}`. See ADR-0001 D5."""\n'},
    )
    found, _, opted_out = mod.scan(root)
    assert opted_out == [], "documenting the escape must not take the escape"
    assert [f.marker for f in found] == ["D5"]


def test_the_checker_does_not_opt_itself_out():
    """Spelled out as one literal, the sentinel excludes the file that defines it.

    That bug shipped twice here: once written plainly, once as an implicitly
    concatenated pair that `ruff format` silently re-joined. Hence the f-string —
    and hence this pin, since the formatter runs on every commit.
    """
    assert mod.OPT_OUT not in SCRIPT.read_text(encoding="utf-8")


# --- exit codes -------------------------------------------------------------


def test_main_is_green_on_a_clean_corpus(tmp_path, capsys):
    root = corpus(
        tmp_path,
        {"0001-a.md": "# ADR-0001\n\n### D1 — plain\n"},
        {"m.py": '"""See ADR-0001 D1."""\n'},
    )
    assert mod.main([str(root)]) == 0
    err = capsys.readouterr().err
    assert "not covered:" in err, "the narrowing must be stated on every run"


def test_main_is_red_on_a_dangling_citation(tmp_path):
    root = corpus(
        tmp_path,
        {"0001-a.md": "# ADR-0001\n\n### D1 — plain\n"},
        {"m.py": '"""See ADR-0001 D5."""\n'},
    )
    assert mod.main([str(root)]) == 1
