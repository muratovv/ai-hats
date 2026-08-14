"""HATS-1642 — the one-shot ticket that carries the supervisor's answer.

Every property here is checked by BEHAVIOUR, never by an assertion about a
constant: a ticket is one-shot because spending it twice fails, bound to a card
because another card's ticket does not open it, and expiring because a stale one
is refused. The HATS-1647 lesson — a requirement asserted against itself is a
requirement nobody checked.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from ai_hats_library.hooks import consent_ticket as ct

REPO_ROOT = Path(__file__).resolve().parents[1]
#: The shipped file under test — named by path so the gate-coverage ratchet can
#: see it, and asserted so a move fails here rather than going quiet.
GATE = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/hooks/consent_ticket.py"


def test_the_shipped_gate_file_is_where_the_hook_expects_it():
    assert GATE.is_file(), f"consent_ticket.py moved — the hook sibling breaks: {GATE}"
    assert Path(ct.__file__).resolve() == GATE.resolve()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """A checkout whose git dir is a plain directory — no git binary needed."""
    (tmp_path / ".git").mkdir()
    monkeypatch.delenv(ct.TICKET_ENV, raising=False)
    return tmp_path


def _grant(nonce: str, monkeypatch) -> None:
    monkeypatch.setenv(ct.TICKET_ENV, nonce)


def test_a_minted_ticket_is_spent_once_and_only_once(repo, monkeypatch):
    nonce = ct.mint("HATS-1", start=repo)
    _grant(nonce, monkeypatch)

    assert ct.consume("HATS-1", start=repo) is True
    assert ct.consume("HATS-1", start=repo) is False, "the ticket was reusable"


def test_a_ticket_does_not_open_another_card(repo, monkeypatch):
    nonce = ct.mint("HATS-1", start=repo)
    _grant(nonce, monkeypatch)

    assert ct.consume("HATS-2", start=repo) is False, "a foreign card's ticket passed"
    # …and refusing HATS-2 did not eat the consent HATS-1 was given.
    assert ct.consume("HATS-1", start=repo) is True


def _age(path, seconds: float) -> None:
    """Backdate a ticket on both axes a reader could judge it by."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["issued_at"] = data["issued_at"] - seconds
    path.write_text(json.dumps(data), encoding="utf-8")
    when = time.time() - seconds
    os.utime(path, (when, when))


def test_a_ticket_does_not_expire_while_the_supervisor_thinks(repo, monkeypatch):
    """The question hangs until it is answered — a supervisor requirement.

    There is no callback after the answer, so the ticket is written when the
    question is RAISED. That made the old 90s window a budget for thinking:
    whoever thought longer lost the click and had to be asked again. Time is no
    longer an axis of validity; the card, the session, the argv and the one-shot
    unlink are what hold, and none of them weakens with the clock (HATS-1682).
    """
    nonce = ct.mint("HATS-1", start=repo, argv=_A)
    _age(ct.tickets_dir(repo) / f"{nonce}.json", 3600)
    _grant(nonce, monkeypatch)

    assert ct.consume("HATS-1", start=repo, argv=_A) is True


#: What the supervisor was shown, as argv past the binary — the form both sides
#: can name (the hook from its lexer, the rack process from its own argv).
_A = ["transition", "HATS-1", "execute"]
_B = ["transition", "HATS-1", "execute", "--json"]


def test_a_ticket_does_not_travel_to_another_command(repo, monkeypatch):
    """Consent is for the call the supervisor read, not for the card in general.

    Without this a ticket left over from a REJECTED question — the mint precedes
    the answer, so one always is — would still open the next call in its window.
    """
    nonce = ct.mint("HATS-1", start=repo, argv=_A)
    _grant(nonce, monkeypatch)

    assert ct.consume("HATS-1", start=repo, argv=_B) is False, "another command spent it"
    # …and refusing B did not eat the consent that was given to A.
    assert ct.consume("HATS-1", start=repo, argv=_A) is True


def test_the_same_command_typed_untidily_is_the_same_command(repo, monkeypatch):
    """The binding is on argv, not on the raw line, so spacing and quote style —
    the shell's business, not the supervisor's — never cause a false refusal."""
    nonce = ct.mint("HATS-1", start=repo, argv=["transition", "HATS-1", "--log", "a b"])
    _grant(nonce, monkeypatch)

    assert ct.consume("HATS-1", start=repo, argv=["transition", "HATS-1", "--log", "a b"]) is True


def test_a_ticket_does_not_travel_to_another_session(repo, monkeypatch):
    """A ticket the supervisor answered in one session — or REJECTED there, since
    the mint precedes the answer — is not consent anywhere else (HATS-1642 R1)."""
    monkeypatch.setenv("AI_HATS_SESSION_ID", "session-a")
    nonce = ct.mint("HATS-1", start=repo)
    _grant(nonce, monkeypatch)

    monkeypatch.setenv("AI_HATS_SESSION_ID", "session-b")
    assert ct.consume("HATS-1", start=repo) is False, "another session spent the ticket"

    monkeypatch.setenv("AI_HATS_SESSION_ID", "session-a")
    assert ct.consume("HATS-1", start=repo) is True, "the issuing session lost its own ticket"


def test_the_sweep_window_is_not_a_budget_for_thinking(repo):
    """The remaining number is a housekeeping interval, not a consent window —
    so it must be far longer than any answer could plausibly take."""
    assert ct.STALE_AFTER_SECONDS >= 3600, ct.STALE_AFTER_SECONDS
    assert not hasattr(ct, "TTL_SECONDS"), "the expiry axis is back"


def test_an_invented_nonce_opens_nothing(repo, monkeypatch):
    ct.mint("HATS-1", start=repo)
    _grant("f" * 32, monkeypatch)

    assert ct.consume("HATS-1", start=repo) is False


def test_a_traversal_shaped_value_is_never_a_path(repo, monkeypatch):
    """The env value is caller-shaped input; `../` must not reach the filesystem.

    Aimed at the EXACT depth the store sits at (``<git>/ai-hats/consent/<n>.json``)
    and baited with a ticket this very call would otherwise accept — so with the
    shape check gone the traversal deletes a file two levels up and this fails.
    """
    ct.mint("HATS-1", start=repo)  # the store must exist, or `..` resolves nowhere
    bait = repo / ".git" / "escape.json"
    bait.write_text(
        json.dumps({"task_id": "HATS-1", "issued_at": time.time(), "session_id": ""}),
        encoding="utf-8",
    )
    _grant("../../escape", monkeypatch)

    assert ct.consume("HATS-1", start=repo) is False
    assert bait.exists(), "the traversal value was treated as a path"


def test_spending_a_ticket_sweeps_the_stale_ones_out(repo, monkeypatch):
    """A ticket nobody will spend still reads like live consent to anyone
    opening the directory."""
    stale = ct.mint("HATS-OLD", start=repo, argv=_A)
    directory = ct.tickets_dir(repo)
    _age(directory / f"{stale}.json", ct.STALE_AFTER_SECONDS + 60)

    fresh = ct.mint("HATS-1", start=repo, argv=_A)
    _grant(fresh, monkeypatch)
    assert ct.consume("HATS-1", start=repo, argv=_A) is True

    assert not (directory / f"{stale}.json").exists(), "the stale ticket outlived the sweep"


def test_the_engine_sweeps_a_store_the_agent_may_not_touch(repo):
    """`mint` and `consume` were the only sweeps, so a refused ticket lay on
    disk until the NEXT gated transition — measured at 1831s in a session that
    had none. The agent cannot clear it either: `rm` under the store is refused,
    and correctly so, or it could tidy away evidence and forge consent. That
    leaves the engine, once per session (HATS-1682).
    """
    stale = ct.mint("HATS-OLD", start=repo, argv=_A)
    live = ct.mint("HATS-NEW", start=repo, argv=_A)
    directory = ct.tickets_dir(repo)
    _age(directory / f"{stale}.json", ct.STALE_AFTER_SECONDS + 60)

    assert ct.sweep(repo) == 1

    assert not (directory / f"{stale}.json").exists()
    assert (directory / f"{live}.json").exists(), "the sweep took a live ticket"


def test_a_sweep_with_no_store_is_not_an_error(tmp_path):
    assert ct.sweep(tmp_path) == 0


def test_no_ticket_in_the_environment_is_simply_no_consent(repo):
    assert ct.consume("HATS-1", start=repo) is False


def test_an_unwritable_ticket_dir_refuses_instead_of_raising(tmp_path, capsys):
    """A gate that walks the filesystem must not take its neighbours down."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ai-hats").write_text("not a directory", encoding="utf-8")

    assert ct.mint("HATS-1", start=tmp_path) is None
    assert "NOT minted" in capsys.readouterr().err, "the failure went unrecorded"


def test_outside_a_repo_there_is_nowhere_to_mint(tmp_path):
    assert ct.tickets_dir(tmp_path) is None
    assert ct.mint("HATS-1", start=tmp_path) is None
