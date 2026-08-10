"""e2e (HATS-1218)

flow: a developer running CLI commands using an incompatible Python interpreter version
cmds:
    python3.9 -m ai_hats --version
expect: interpreter guard checks Python version and exits with error naming supported
        Python version
why: without interpreter guards, running on unsupported Python versions produces obscure
     runtime errors"""

from __future__ import annotations

from pathlib import Path

from _helpers.interpreter import foreign_source_checkout, remedy


def checkout_path(raw: str) -> Path:
    """A path as the guard reports it — every fixture below goes through here.

    ``foreign_source_checkout`` resolves its inputs, so an expectation built from
    a symlinked prefix never matches: macOS maps ``/tmp`` onto ``/private/tmp``,
    which made this file green on Linux CI and red on every maintainer's machine
    (HATS-1245). One seam, so a platform quirk is tuned in a single place instead
    of at each call site.
    """
    return Path(raw).resolve()


REPO = checkout_path("/work/ai-hats")


def test_editable_install_of_this_checkout_is_accepted():
    resolved = REPO / "src" / "ai_hats" / "__init__.py"
    assert foreign_source_checkout(resolved, REPO) is None


def test_editable_install_of_another_checkout_is_flagged():
    """The worktree trap: tests here, code from the main checkout."""
    other = checkout_path("/tmp/ai-hats-wt-task-hats-1218")
    resolved = other / "src" / "ai_hats" / "__init__.py"
    assert foreign_source_checkout(resolved, REPO) == other


def test_wheel_install_is_accepted():
    """HATS-685's intent: e2e SHOULD exercise a real site-packages install."""
    resolved = checkout_path("/venv/lib/python3.11/site-packages/ai_hats/__init__.py")
    assert foreign_source_checkout(resolved, REPO) is None


def test_remedy_names_both_paths_and_a_runnable_fix():
    other = checkout_path("/tmp/other-checkout")
    msg = remedy(REPO, other)
    assert str(REPO) in msg
    assert str(other) in msg
    assert "uv pip install -e" in msg
    # The pre-commit hook has the same blindness — the message must say so.
    assert "PATH=" in msg
