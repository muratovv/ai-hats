"""Tests for the retired-prune CALL SITE in ``ai_hats._bump_internal`` (HATS-1280).

``retired_dists`` promises never to raise; this file pins what the caller does
when that promise is broken anyway. The prune sits in the middle of
``self update``, ahead of ``do_bump``, so its contract here is one sentence:
**it cannot cost the bump**. ``main`` must still return ``do_bump``'s exit code,
and ``do_bump`` must still run — including when the prune's own *import* is what
fails, which is why the source wraps the import inside the guard too.
"""

from __future__ import annotations

import pytest

from ai_hats import _bump_internal, retired_dists


def _stub_bump(monkeypatch: pytest.MonkeyPatch, code: int) -> list[dict]:
    """Replace ``do_bump`` with a recorder — its return value is the contract."""
    calls: list[dict] = []

    def do_bump(*, migrate_force: bool = False, check_branches: bool = False) -> int:
        calls.append({"migrate_force": migrate_force, "check_branches": check_branches})
        return code

    monkeypatch.setattr("ai_hats.cli.assembly.do_bump", do_bump)
    return calls


def test_prune_raising_baseexception_does_not_change_the_exit_code(monkeypatch, capsys):
    """A ``BaseException`` out of the prune is absorbed at the call site.

    ``retired_dists.prune_retired`` already swallows everything, so this pins the
    SECOND net — the one that still holds if that first one is ever removed.
    """

    def boom(_project_dir):
        raise KeyboardInterrupt

    monkeypatch.setattr(retired_dists, "prune_retired", boom)
    calls = _stub_bump(monkeypatch, code=7)

    assert _bump_internal.main([]) == 7
    assert calls == [{"migrate_force": False, "check_branches": False}], "do_bump did not run"
    assert "prune skipped" in capsys.readouterr().err


def test_each_removed_item_is_reported_on_stderr(monkeypatch, capsys):
    """One line per removal — the only trace the user gets that a CLI vanished."""
    monkeypatch.setattr(retired_dists, "prune_retired", lambda _p: ["ai-hats-tracker", "/v/bin/x"])
    _stub_bump(monkeypatch, code=0)

    assert _bump_internal.main([]) == 0

    err = capsys.readouterr().err
    assert "ai-hats: removed retired ai-hats-tracker" in err
    assert "ai-hats: removed retired /v/bin/x" in err
    assert err.count("removed retired") == 2, "expected exactly one line per removed item"


def test_an_unimportable_prune_still_lets_the_bump_run(monkeypatch, capsys):
    """The import lives INSIDE the guard: ``retired_dists`` reaches ``_bootstrap``,
    so an ImportError here would otherwise skip the re-assembly entirely.

    ``delattr`` reproduces the shape exactly — ``from .retired_dists import
    prune_retired`` against a module missing the name raises ``ImportError``.
    """
    monkeypatch.delattr(retired_dists, "prune_retired")
    calls = _stub_bump(monkeypatch, code=1)

    assert _bump_internal.main([]) == 1
    assert calls == [{"migrate_force": False, "check_branches": False}], "do_bump did not run"
    assert "prune skipped" in capsys.readouterr().err


def test_flags_still_reach_do_bump_across_the_prune(monkeypatch):
    """The prune block sits between argv parsing and ``do_bump`` — it must be transparent."""
    monkeypatch.setattr(retired_dists, "prune_retired", lambda _p: [])
    calls = _stub_bump(monkeypatch, code=0)

    assert _bump_internal.main(["--migrate-force", "--check-branches"]) == 0
    assert calls == [{"migrate_force": True, "check_branches": True}]


def test_unknown_flag_short_circuits_before_the_prune_runs(monkeypatch):
    """Arg validation precedes the prune: a typo must not uninstall anything."""

    def boom(_project_dir):
        raise AssertionError("prune_retired ran despite an unknown flag")

    monkeypatch.setattr(retired_dists, "prune_retired", boom)
    calls = _stub_bump(monkeypatch, code=0)

    assert _bump_internal.main(["--nope"]) == 2
    assert calls == [], "do_bump ran on an unknown flag"
