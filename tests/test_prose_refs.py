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
    (root / "src" / "pkg" / "real.py").write_text("class Widget:\n    def build(self):\n        pass\n")
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


def test_real_repository_scan_reports_its_own_reach() -> None:
    """The checker must be able to say what it did NOT judge. A gate that reports
    only findings cannot be distinguished from one that looked at nothing."""
    findings, _, unanchored, files = prose_refs.scan(prose_refs.REPO_ROOT)
    assert files > 100, files
    assert unanchored > 0, "an all-anchored corpus means the anchor test is broken"
    assert isinstance(findings, list)
