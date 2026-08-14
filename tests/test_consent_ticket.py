"""HATS-1642 — the one-shot ticket that carries the supervisor's answer.

Every property here is checked by BEHAVIOUR, never by an assertion about a
constant: a ticket is one-shot because spending it twice fails, bound to a card
because another card's ticket does not open it, and expiring because a stale one
is refused. The HATS-1647 lesson — a requirement asserted against itself is a
requirement nobody checked.
"""

from __future__ import annotations

import json
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


def test_a_ticket_older_than_its_ttl_is_refused(repo, monkeypatch):
    nonce = ct.mint("HATS-1", start=repo)
    _grant(nonce, monkeypatch)
    stale = ct.TTL_SECONDS + 1

    import time

    assert ct.consume("HATS-1", start=repo, now=time.time() + stale) is False
    assert ct.consume("HATS-1", start=repo) is False, "an expired ticket survived"


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


def test_the_ttl_is_short_enough_that_a_stale_click_is_not_consent(repo):
    """A number, but a load-bearing one: the mint happens when the question is
    RAISED, so the TTL is the whole window a rejected ticket stays usable."""
    assert 60 <= ct.TTL_SECONDS <= 120, ct.TTL_SECONDS


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
