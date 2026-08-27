"""A shell redirect token is not a path — the two faces of one defect.

`shlex(punctuation_chars=True)` splits `2>&1` into `['2', '>&', '1']`, and both
halves of `check_backlog_write` read what follows a redirect operator as a
filename. The two faces point opposite ways:

* the redirect-target reader refused a bare `1` resolved against the cwd, so the
  gate blocked the very `rack` call it prescribes — but only from a cwd under
  the backlog, which made it read as flakiness;
* the mutator branch slices `paths[-1:]` for `cp`/`ln`, so a trailing `2>&1` put
  the descriptor last and the real destination was never examined at all. That
  one is fail-OPEN: appending ` 2>&1` walked a copy into the tracker.

Scope split. The discriminator is pure, so it is judged here directly. Whether
a descriptor resolves INTO the backlog depends on where the shell stands, and
patching that with `chdir` would be replacing the ambient read the gate exists
to perform — `tests/e2e/test_backlog_write_gate.py` pins it with a real cwd.

Every case below is paired with a control that must NOT move, because "the
false refusal is gone" and "the predicate stopped judging" look identical from
one direction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS = (
    Path(__file__).resolve().parents[1]
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks"
)
sys.path.insert(0, str(HOOKS))

import safety_gate  # noqa: E402
from safety_gate import (  # noqa: E402
    REDIRECTS,
    check_backlog_write,
    parse_commands,
    redirect_file_targets,
    without_shell_redirects,
)

CARD_REL = ".agent/ai-hats/tracker/backlog/tasks/HATS-1/task.yaml"


@pytest.fixture
def card(tmp_path):
    """An absolute card path in a tree the gate's resolver reads as a project."""
    (tmp_path / "ai-hats.yaml").write_text("provider: claude\n")
    path = tmp_path / CARD_REL
    path.parent.mkdir(parents=True)
    path.write_text("id: HATS-1\n")
    return path


def _tokens(command: str):
    return list(parse_commands(command))[0]


def _verdict(command: str) -> str:
    args = _tokens(command)
    return check_backlog_write(args[0], args)


# ----- the discriminator, judged directly -----


@pytest.mark.parametrize("tail", ["2>&1", "1>&2", "2>&-"])
def test_a_duplicated_descriptor_is_not_a_write_target(tail):
    assert redirect_file_targets(_tokens(f"rack transition HATS-1 --log x {tail}")) == []


@pytest.mark.parametrize("op", [">", ">>", ">|", "&>", "&>>", ">&"])
def test_every_operator_still_yields_a_filename_target(op):
    """The control the whole fix turns on: `>&` takes a descriptor OR a name, so
    the discriminator reads the OPERAND. Dropping the operator from `REDIRECTS`
    would pass the row above and lose every row here."""
    assert redirect_file_targets(_tokens(f"echo x {op} /tmp/out.log")) == ["/tmp/out.log"]


def test_the_operator_set_is_the_one_the_discriminator_was_written_against():
    """A new operator must be classified, not silently inherited."""
    assert set(REDIRECTS) == {">", ">>", ">|", "&>", "&>>", ">&"}


def test_the_operator_itself_never_reaches_the_path_list():
    """`2>&-` put the literal `>&` into the mutator branch's paths."""
    args = _tokens("cp /tmp/a /tmp/b 2>&-")
    assert ">&" not in safety_gate._paths(without_shell_redirects(args))


# ----- the destination survives the redirect (the fail-open) -----


def test_a_copy_into_the_backlog_is_denied_despite_a_trailing_redirect(card):
    assert _verdict(f"cp /tmp/a {card} 2>&1"), (
        "appending a redirect must not hide the real destination"
    )


def test_a_copy_into_the_backlog_is_denied_without_one(card):
    """Control: the arm above must not be passing for want of any judging."""
    assert _verdict(f"cp /tmp/a {card}")


def test_a_copy_outside_the_backlog_stays_allowed(tmp_path):
    """The other control — the predicate still says no to nothing in particular."""
    assert not _verdict(f"cp /tmp/a {tmp_path / 'b'} 2>&1")


def test_a_real_file_redirect_into_the_backlog_is_denied(card):
    """End to end through the predicate, not just the discriminator."""
    assert _verdict(f"echo x >&{card}")
