"""A shell redirect token is not a path — the two faces of one defect.

`shlex(punctuation_chars=True)` splits `2>&1` into `['2', '>&', '1']`, and both
halves of `check_backlog_write` used to read what follows a redirect operator as
a filename. The two faces point opposite ways:

* the redirect-target reader refused a bare `1` resolved against the cwd, so the
  gate blocked the very `rack` call it prescribes — but only from a cwd under
  the backlog, which made it read as flakiness;
* the mutator branch slices `paths[-1:]` for `cp`/`ln`, so a trailing `2>&1` put
  the descriptor last and the real destination was never examined at all. That
  one is fail-OPEN: appending ` 2>&1` walked a copy into the tracker.

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
from safety_gate import REDIRECTS, check_backlog_write, parse_commands  # noqa: E402

CARD = ".agent/ai-hats/tracker/backlog/tasks/HATS-1/task.yaml"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A tree the gate's resolver recognises as a project with a backlog."""
    (tmp_path / "ai-hats.yaml").write_text("provider: claude\n")
    card = tmp_path / CARD
    card.parent.mkdir(parents=True)
    card.write_text("id: HATS-1\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _verdict(command: str) -> str:
    args = list(parse_commands(command))[0]
    return check_backlog_write(args[0], args)


# ----- the descriptor is not a file -----


@pytest.mark.parametrize("tail", ["2>&1", "1>&2", "2>&-"])
def test_a_duplicated_descriptor_is_not_a_write_target(project, monkeypatch, tail):
    """From a cwd under the backlog, a bare `1` used to resolve INTO it."""
    monkeypatch.chdir(project / CARD.rsplit("/", 1)[0])
    assert not _verdict(f"rack transition HATS-1 --log x {tail}"), (
        "a file descriptor is not a path the gate may judge"
    )


def test_a_clean_rack_call_is_allowed_from_the_same_cwd(project, monkeypatch):
    """Control for the row above: it must pass for the ordinary reason too."""
    monkeypatch.chdir(project / CARD.rsplit("/", 1)[0])
    assert not _verdict("rack transition HATS-1 --log x")


# ----- and the file after `>&` still is one -----


@pytest.mark.parametrize(
    "command",
    [
        f"echo x > {CARD}",
        f"echo x >> {CARD}",
        f"echo x >&{CARD}",
        f"echo x &> {CARD}",
    ],
)
def test_a_real_file_redirect_into_the_backlog_is_denied(project, command):
    """`>&` takes either a descriptor or a filename. Discriminating on the
    OPERAND keeps this arm; dropping the operator from `REDIRECTS` would not."""
    assert _verdict(command), f"a real redirect into the tracker must be denied: {command}"


# ----- the destination survives the redirect (the fail-open) -----


def test_a_copy_into_the_backlog_is_denied_despite_a_trailing_redirect(project):
    assert _verdict(f"cp /tmp/a {CARD} 2>&1"), (
        "appending a redirect must not hide the real destination"
    )


def test_a_copy_into_the_backlog_is_denied_without_one(project):
    """Control: the arm above must not be passing for want of any judging."""
    assert _verdict(f"cp /tmp/a {CARD}")


def test_a_copy_outside_the_backlog_stays_allowed(project):
    """The other control — the predicate still says no to nothing in particular."""
    assert not _verdict("cp /tmp/a /tmp/b 2>&1")


def test_the_operator_itself_never_becomes_a_path(project):
    """`2>&-` put the literal `>&` into the mutator's path list."""
    args = list(parse_commands(f"cp /tmp/a {CARD} 2>&-"))[0]
    assert ">&" not in safety_gate._paths(safety_gate.without_shell_redirects(args))


def test_every_redirect_operator_is_covered_by_the_discriminator():
    """A new operator added to `REDIRECTS` must be classified, not inherited."""
    assert set(REDIRECTS) == {">", ">>", ">|", "&>", "&>>", ">&"}
