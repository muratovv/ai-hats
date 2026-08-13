"""The sub-agent case: cwd is a linked worktree, the pin names the main checkout.

HATS-1613 / ADR-0025 D2. ``subagent_runner`` hands the child ``AI_HATS_PROJECT_DIR``
= the MAIN checkout while the child executes in a worktree, so ``cwd != pin`` is
designed and permanent. What reconciles them is the worktree-hop — and *because*
the contract makes the hop load-bearing, these tests exercise the real one rather
than monkeypatching it (`test_cli_helpers.py` already covers the branching with a
stub; a stubbed hop cannot catch a broken hop). The worktree is handed in via
``_project_dir(start=…)`` rather than by `chdir`-ing the process — exit 1 of
`scripts/check_test_isolation.py`.

Net for the trust-policy work in the same task: if resolution stops hopping, a
legitimate pin starts reading as foreign, ``AI_HATS_DIR`` gets dropped, and every
sub-agent writes past the tracker.
"""  # comment-length: allow — the scenario is the contract

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

from ai_hats.cli._helpers import _project_dir
from ai_hats.env import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
from ai_hats.paths import ai_hats_dir


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def _project(path: Path) -> Path:
    """A real git repo that is also an onboarded ai-hats project."""
    path.mkdir(parents=True)
    _git(path, "init")
    _git(path, "config", "user.email", "t@t.co")
    _git(path, "config", "user.name", "t")
    _git(path, "commit", "--allow-empty", "-m", "init")
    (path / ".agent" / "ai-hats").mkdir(parents=True)
    return path


def _pin(monkeypatch, project: Path) -> None:
    monkeypatch.setenv(AI_HATS_PROJECT_DIR_ENV, str(project))
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(project / ".agent" / "ai-hats"))


def test_worktree_cwd_with_main_checkout_pin_resolves_to_main(tmp_path, monkeypatch):
    """The designed sub-agent layout resolves to the main checkout, silently."""
    main = _project(tmp_path / "main")
    wt = tmp_path / "linked"
    _git(main, "worktree", "add", str(wt))
    _pin(monkeypatch, main)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = _project_dir(start=wt)
        base = ai_hats_dir(resolved)

    assert resolved.resolve() == main.resolve(), (
        "the hop did not fire: a sub-agent in a worktree would resolve to the "
        "worktree and treat its own legitimate pin as foreign"
    )
    assert base.resolve() == (main / ".agent" / "ai-hats").resolve()
    assert not [w for w in caught if "foreign" in str(w.message)], (
        f"a same-project pin must not warn: {[str(w.message) for w in caught]}"
    )


def test_worktree_cwd_with_a_genuinely_foreign_pin_still_warns(tmp_path, monkeypatch):
    """Negative control — the guard must stay armed for a real foreign pin."""
    main = _project(tmp_path / "main")
    other = _project(tmp_path / "other")
    wt = tmp_path / "linked"
    _git(main, "worktree", "add", str(wt))
    _pin(monkeypatch, other)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        base = ai_hats_dir(_project_dir(start=wt))

    assert [w for w in caught if "foreign" in str(w.message)], (
        "a pin naming an unrelated project must warn"
    )
    assert base.resolve() == (main / ".agent" / "ai-hats").resolve(), (
        "the foreign override must be ignored in favour of the resolved project"
    )


def test_worktree_carrying_its_own_agent_dir_resolves_to_itself(tmp_path, monkeypatch):
    """Characterization, not endorsement — pins today's answer for the fragile case.

    ``_project_dir`` checks ``.agent/`` BEFORE the hop, and its docstring justifies
    that with "a worktree carries neither the gitignored .agent/ nor the untracked
    ai-hats.yaml". Nothing enforces that premise. Should a worktree ever acquire
    ``.agent/``, resolution stops hopping and the session's own pin reads as
    foreign. The behaviour is at least loud (the guard warns); this test exists so
    a change to either half is a deliberate edit rather than a silent drift.
    """
    main = _project(tmp_path / "main")
    wt = tmp_path / "linked"
    _git(main, "worktree", "add", str(wt))
    (wt / ".agent" / "ai-hats").mkdir(parents=True)
    _pin(monkeypatch, main)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = _project_dir(start=wt)
        base = ai_hats_dir(resolved)

    assert resolved.resolve() == wt.resolve()
    assert [w for w in caught if "foreign" in str(w.message)], (
        "silence here would be the defect: the session's pin is being discarded"
    )
    assert base.resolve() == (wt / ".agent" / "ai-hats").resolve()
