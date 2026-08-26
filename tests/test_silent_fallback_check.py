"""HATS-1373 — the gate that refuses a handler nothing can escape from.

The first attempt at this class defined the defect as "broad catch that does not
log", against a dictionary of logging shapes. Two real handlers show why that
does not hold: one reports via a domain call, one via a returned value. Both are
fine, both would have been flagged. The fixtures below pin that distinction.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_silent_fallback.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_silent_fallback", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


def flagged(body: str) -> list[int]:
    """Lines flagged in a module built from one try/except body."""
    return [v.line for v in mod.violations(body, "fixture.py")]


# --- the defect ------------------------------------------------------------


@pytest.mark.parametrize(
    "handler",
    [
        "except Exception:\n        pass",
        "except Exception:\n        return []",
        "except BaseException:\n        return None",
        "except:\n        pass",
        "except (ValueError, Exception):\n        pass",
        "except Exception as exc:\n        result = None",
        "except Exception:\n        return {}",
    ],
)
def test_an_inert_broad_handler_is_flagged(handler):
    assert flagged(f"def f():\n    try:\n        work()\n    {handler}\n") == [4]


def test_every_inert_handler_in_a_module_is_reported():
    source = (
        "try:\n    a()\nexcept Exception:\n    pass\ntry:\n    b()\nexcept Exception:\n    pass\n"
    )

    assert flagged(source) == [3, 7]


# --- reporting, in each shape it really takes -------------------------------


def test_a_logging_handler_passes():
    source = "try:\n    work()\nexcept Exception:\n    logger.warning('failed')\n"

    assert flagged(source) == []


def test_a_handler_that_reports_via_a_domain_call_passes():
    """The rack_context.py:306 shape — no logger in sight, still reports."""
    source = "try:\n    work()\nexcept Exception as exc:\n    handle_rack_error(exc, as_json)\n    return\n"

    assert flagged(source) == []


def test_a_handler_that_reports_via_a_returned_value_passes():
    """The dispatch.py:368 shape — the failure leaves as data, not as a call."""
    source = (
        "try:\n    work()\n"
        "except Exception as exc:\n"
        "    outcomes.append(SubscriberOutcome(sub.name, 'error', reason=repr(exc)))\n"
        "    continue\n"
    )

    assert flagged(source) == []


def test_a_handler_that_returns_the_exception_in_a_message_passes():
    """The migration/core.py:162 shape — reports with no call at all, via an f-string."""
    source = (
        "try:\n    work()\nexcept Exception as exc:\n"
        '    return False, f"not loadable as a rack card: {exc}"\n'
    )

    assert flagged(source) == []


def test_an_unused_exception_binding_is_still_inert():
    """Binding a name you never read reports nothing."""
    source = "try:\n    work()\nexcept Exception as exc:\n    return None\n"

    assert flagged(source) == [3]


def test_a_re_raising_handler_passes():
    assert flagged("try:\n    work()\nexcept Exception:\n    raise\n") == []


def test_a_handler_that_raises_something_else_passes():
    source = "try:\n    work()\nexcept Exception as exc:\n    raise RuntimeError('nope') from exc\n"

    assert flagged(source) == []


# --- breadth ---------------------------------------------------------------


@pytest.mark.parametrize("caught", ["OSError", "(OSError, ValueError)", "KeyError"])
def test_a_narrow_handler_is_never_flagged(caught):
    """Deliberately out of scope: a narrow except documents the expected case."""
    source = f"try:\n    work()\nexcept {caught}:\n    pass\n"

    assert flagged(source) == []


def test_a_narrow_tuple_containing_exception_is_broad():
    source = "try:\n    work()\nexcept (OSError, Exception):\n    pass\n"

    assert flagged(source) == [3]


# --- the marker ------------------------------------------------------------


def test_the_marker_on_the_except_line_accepts_the_site():
    source = "try:\n    work()\nexcept Exception:  # silent-ok: teardown, logging may itself throw\n    pass\n"

    assert flagged(source) == []


def test_the_marker_inside_the_body_accepts_the_site():
    source = "try:\n    work()\nexcept Exception:\n    # silent-ok: best-effort cleanup\n    pass\n"

    assert flagged(source) == []


def test_a_noqa_comment_is_not_a_marker():
    """`# noqa: BLE001` justifies the breadth, never the silence — 18 sites carry one."""
    source = "try:\n    work()\nexcept Exception:  # noqa: BLE001 - best-effort\n    pass\n"

    assert flagged(source) == [3]


# --- file selection --------------------------------------------------------


def test_test_trees_are_not_scanned():
    scanned = {p.relative_to(REPO_ROOT).as_posix() for p in mod.source_files(REPO_ROOT)}

    assert scanned, "no source files discovered at all"
    assert not [p for p in scanned if "tests/" in p], "a test tree leaked into the scan"


def test_every_workspace_package_is_scanned():
    scanned = {p.relative_to(REPO_ROOT).as_posix() for p in mod.source_files(REPO_ROOT)}

    assert any(p.startswith("src/ai_hats/") for p in scanned)
    assert any(p.startswith("packages/ai-hats-rack/") for p in scanned)
    # HATS-1826 folded the surfaces in, so what once nested under packages/surfaces/
    # is now an area under src/ and has to be reached at that depth instead.
    assert any(p.startswith("src/ai_hats/surfaces/") for p in scanned)
    assert any(p.startswith("scripts/") for p in scanned)


# --- the real repo ---------------------------------------------------------


def test_this_repo_has_no_inert_broad_handler():
    found = mod.scan(REPO_ROOT)

    assert not found, "silent fallbacks:\n" + "\n".join(str(v) for v in found)
