"""HATS-1825 — the prose-refs checker, on a corpus small enough to reason about.

Every resolver is asserted in BOTH directions in the same test: the dead form is
found and the live form is not. A one-directional assertion cannot tell a working
checker from one that flags everything, which is the failure this gate exists to
catch in prose and would be embarrassing to reproduce in its own tests.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_prose_refs as prose_refs  # noqa: E402

LIB = prose_refs.LIBRARY_RELPATH


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature repository: `src/` and `docs/` are the tracked anchors."""
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src" / "pkg" / "real.py").write_text(
        "class Widget:\n    def build(self):\n        pass\n"
    )
    (root / "docs" / "guide.md").write_text("# guide\n")
    (root / LIB / "core" / "skills" / "demo").mkdir(parents=True)
    (root / LIB / "core" / "rules" / "demo_rule").mkdir(parents=True)
    (root / LIB / "core" / "traits" / "demo-trait").mkdir(parents=True)
    (root / LIB / "core" / "traits" / "demo-trait" / "config.yaml").write_text(
        "injection: |\n  ### A Real Heading\n  body\n"
    )
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return root


def _skill(repo: Path, body: str) -> None:
    path = repo / LIB / "core" / "skills" / "demo" / "SKILL.md"
    path.write_text(f"---\nname: demo\n---\n\n{body}\n")


def _findings(repo: Path) -> list[str]:
    return [f"{f.token} :: {f.message}" for f in prose_refs.scan(repo)[0]]


def test_anchored_path_dead_is_found_live_is_not(repo: Path) -> None:
    _skill(repo, "See `src/pkg/gone.py` and also `src/pkg/real.py` for detail.")
    found = _findings(repo)
    assert any("src/pkg/gone.py" in f for f in found), found
    assert not any("src/pkg/real.py" in f for f in found), found


def test_unanchored_path_is_counted_not_flagged(repo: Path) -> None:
    """`cmd/myapp/` in a Go teaching skill is not a claim about this tree."""
    _skill(repo, "Put the binary in `cmd/myapp/` as usual.")
    findings, _, unanchored, _ = prose_refs.scan(repo)
    assert findings == []
    assert unanchored == 1


def test_stale_library_prefix_is_found_live_alias_is_not(repo: Path) -> None:
    _skill(repo, "Components live in `library/core/` — reach them at `$LIB/core/`.")
    found = _findings(repo)
    assert any("library/core/" in f and "stale prefix" in f for f in found), found
    assert not any("$LIB/core/" in f for f in found), found


def test_stale_prefix_message_separates_moved_from_missing(repo: Path) -> None:
    """A tail that resolves means "moved"; one that does not means "gone" — the
    two need different fixes, so they must not share a message."""
    _skill(repo, "`library/core/` and `library/nowhere/` differ.")
    found = _findings(repo)
    moved = next(f for f in found if "library/core/" in f)
    missing = next(f for f in found if "library/nowhere/" in f)
    assert "resolves under" in moved
    assert "names nothing under" in missing


def test_dangling_section_reference_is_found_live_one_is_not(repo: Path) -> None:
    _skill(repo, 'See `demo-trait` § "Nowhere At All" and `demo-trait` § "A Real Heading".')
    found = _findings(repo)
    assert any("Nowhere At All" in f for f in found), found
    assert not any("A Real Heading" in f for f in found), found


def test_dangling_symbol_is_found_real_one_and_foreign_one_are_not(repo: Path) -> None:
    """`Path.cwd` belongs to somebody else's vocabulary — an unknown class is a
    gap in our index, never a finding."""
    _skill(repo, "Call `Widget.build`, not `Widget.vanish`; `Path.cwd` is stdlib.")
    found = _findings(repo)
    assert any("Widget.vanish" in f for f in found), found
    assert not any("Widget.build" in f or "Path.cwd" in f for f in found), found


def test_fenced_code_block_is_a_sample_not_a_claim(repo: Path) -> None:
    _skill(repo, "Example:\n\n```bash\ncat `src/pkg/gone.py`\n```\n")
    assert _findings(repo) == []


def test_opt_out_sentinel_skips_the_whole_file(repo: Path) -> None:
    _skill(repo, f"<!-- {prose_refs.OPT_OUT} -->\n\nSee `src/pkg/gone.py`.")
    findings, opted_out, _, _ = prose_refs.scan(repo)
    assert findings == []
    assert len(opted_out) == 1


def test_gitignored_path_is_runtime_state_not_corpus(repo: Path) -> None:
    (repo / ".gitignore").write_text(".agent/\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "ignore")
    _skill(repo, "The retro lands at `.agent/sessions/never-written.md`.")
    assert _findings(repo) == []


def test_placeholder_becomes_a_glob(repo: Path) -> None:
    """`<name>` is what the reader fills in; the machine reads it as `*`."""
    _skill(repo, "Rules live at `library/core/rules/<name>/` — and skills too.")
    found = _findings(repo)
    assert any("resolves under" in f for f in found), found


# ---- The docs corpus, its two escapes, and member resolution ----


def _doc(repo: Path, rel: str, body: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{body}\n")


def test_a_living_doc_is_judged_a_dated_record_is_not(repo: Path) -> None:
    """The docs a human reads make the same path claims library prose does."""
    _doc(repo, "docs/guide.md", "The runner lives in `src/pkg/gone.py`.")
    _doc(repo, "docs/adr/0001-x.md", "We moved it out of `src/pkg/gone.py`.")
    _doc(repo, "docs/migration-v9.9.9.md", "It used to be `src/pkg/gone.py`.")
    found = [f"{f.path} :: {f.token}" for f in prose_refs.scan(repo)[0]]
    assert any("docs/guide.md" in f for f in found), found
    assert not any("adr" in f or "migration" in f for f in found), found


def test_readme_and_contributing_are_in_the_corpus(repo: Path) -> None:
    _doc(repo, "README.md", "Start at `src/pkg/gone.py`.")
    _doc(repo, "CONTRIBUTING.md", "Then read `src/pkg/real.py`.")
    found = [f"{f.path} :: {f.token}" for f in prose_refs.scan(repo)[0]]
    assert any("README.md" in f for f in found), found
    assert not any("CONTRIBUTING.md" in f for f in found), found


def test_was_marker_exempts_its_line_only(repo: Path) -> None:
    """A living doc still has to be able to name what it retired."""
    _doc(
        repo,
        "docs/guide.md",
        "Retired: `src/pkg/gone.py`. %s\n\nStill claimed: `src/pkg/also_gone.py`."
        % "<!-- prose-refs: was -->",
    )
    found = _findings(repo)
    assert not any("src/pkg/gone.py" in f for f in found), found
    assert any("src/pkg/also_gone.py" in f for f in found), found


def test_a_quoted_marker_does_not_exempt_the_line_quoting_it(repo: Path) -> None:
    """The doc that documents the escape must not silently take it."""
    _doc(
        repo,
        "docs/guide.md",
        "Write `<!-- prose-refs: was -->` on such a line — see `src/pkg/gone.py`.",
    )
    assert any("src/pkg/gone.py" in f for f in _findings(repo)), _findings(repo)


def test_a_field_is_a_member_a_signature_parameter_is_not(repo: Path) -> None:
    """A regex over the file read a parameter and a dict key as members, so a
    reference to a field that does not exist passed (the def-only pattern read
    every real field as a defect — both directions are asserted here)."""
    (repo / "src" / "pkg" / "model.py").write_text(
        "class Card:\n"
        "    extras: dict = {}\n"
        "    def build(self, nowhere: str) -> None:\n"
        "        local: int = 1\n"
        "        cfg = {alsonowhere: 1}\n"
    )
    _skill(
        repo,
        "`Card.extras` and `Card.build` exist; `Card.nowhere`, `Card.local` "
        "and `Card.alsonowhere` do not.",
    )
    found = _findings(repo)
    for live in ("Card.extras", "Card.build"):
        assert not any(live in f for f in found), found
    for dead in ("Card.nowhere", "Card.local", "Card.alsonowhere"):
        assert any(dead in f for f in found), found


def test_real_repository_scan_reports_its_own_reach() -> None:
    """The checker must be able to say what it did NOT judge. A gate that reports
    only findings cannot be distinguished from one that looked at nothing."""
    findings, _, unanchored, files = prose_refs.scan(prose_refs.REPO_ROOT)
    assert files > 100, files
    assert unanchored > 0, "an all-anchored corpus means the anchor test is broken"
    assert isinstance(findings, list)
