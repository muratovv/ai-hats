"""Field-owning extensions for HYP/PROP (HATS-1044, ADR-0017 §4/§5).

``hyp-verdicts`` owns ``validation_log`` and ``prop-votes`` owns ``votes``: both
append via ``Delta.fields`` on the composite-transition path, so an append can
ride a state change under ONE lock/persist (the tracker's append-then-set-status
atomicity). ``hyp-verdicts`` also drives the quorum autoclose sweep. Both are
ambient subscribers with no subscriptions — they exist for their python API and
field ownership, reached via ``Workspace.extension``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import click

from ..cli_common import (
    ENV_SESSION_ID,
    JSON_OPT,
    TASKS_DIR_OPT,
    actor,
    emit_json,
    handle_rack_error,
    resolved_root,
)
from ..dispatch import Append
from ..fsm import InvalidTransitionError
from ..models import TaskCard, utc_now
from ..ops import FieldsOp, StateOp
from .quorum import (
    AUTO_SESSION_ID,
    AUTOCLOSE_ACTOR,
    DEFAULT_QUORUM_K,
    QuorumClosure,
    quorum_closures,
)


def _active_logs(tasks_dir: Path) -> Iterable[tuple[str, list]]:
    """``(id, validation_log)`` for every ACTIVE card in the catalog; a corrupt
    card is skipped, not fatal (the listing tolerance, HATS-1024)."""
    if not tasks_dir.is_dir():
        return
    for path in sorted(tasks_dir.glob("*/task.yaml")):
        try:
            card = TaskCard.from_yaml(path)
        except Exception:  # noqa: BLE001, S112 — one corrupt card must not sink the sweep
            continue
        if card.state == "active":
            yield card.id, list(card.extras.get("validation_log") or [])


class HypVerdictsExtension:
    """Owns ``validation_log``; the append + autoclose python API (ADR-0017 §5)."""

    name = "hyp-verdicts"

    def __init__(self) -> None:
        self._kernel: Any = None

    def subscriptions(self):
        return ()

    def on_event(self, ctx):
        return None

    def bind(self, kernel: Any) -> None:
        self._kernel = kernel

    # ----- append API (io.append_verdict / append_then_set_status parity) -----

    def append_verdict(self, hyp_id: str, entry: Mapping[str, Any], *, actor: str, caller_cwd: Path):
        """Append one validation_log entry atomically (no state change)."""
        return self._kernel.transition_ops(
            hyp_id,
            [FieldsOp({"validation_log": Append(dict(entry))})],
            actor=actor,
            caller_cwd=caller_cwd,
        )

    def append_then_set_status(
        self,
        hyp_id: str,
        entry: Mapping[str, Any],
        *,
        to_state: str,
        actor: str,
        caller_cwd: Path,
        reason: str = "",
    ):
        """Append a verdict AND ride the transition to ``to_state`` under ONE
        lock/persist. Returns ``None`` when the edge is no longer legal from the
        card's current state (the only_if_status concurrency/idempotency guard)."""
        try:
            return self._kernel.transition_ops(
                hyp_id,
                [FieldsOp({"validation_log": Append(dict(entry))}), StateOp(to_state)],
                actor=actor,
                caller_cwd=caller_cwd,
                reason=reason,
            )
        except InvalidTransitionError:
            return None

    # ----- quorum autoclose sweep (workspace/pipeline API) --------------------

    def find_quorum_closures(self, *, k: int = DEFAULT_QUORUM_K) -> list[QuorumClosure]:
        """Active HYPs with at least ``k`` independent refuted sessions."""
        return quorum_closures(_active_logs(self._kernel.tasks_dir), k)

    def autoclose(
        self,
        *,
        caller_cwd: Path,
        k: int = DEFAULT_QUORUM_K,
        actor: str = AUTOCLOSE_ACTOR,
        dry_run: bool = False,
        now: str | None = None,
    ) -> list[QuorumClosure]:
        """Close every quorum-reached active HYP (append a synthetic sentinel
        entry + refute, atomically). Idempotent (only-active scan + sentinel
        exclusion), dry-run returns the closures without writing. A closure the
        atomic guard skips (already closed) is excluded from the result."""
        closures = self.find_quorum_closures(k=k)
        if dry_run:
            return closures
        closed: list[QuorumClosure] = []
        for closure in closures:
            result = self.append_then_set_status(
                closure.hyp_id,
                _synthetic_entry(closure, now=now),
                to_state="refuted",
                actor=actor,
                caller_cwd=caller_cwd,
                reason="quorum autoclose",
            )
            if result is not None:
                closed.append(closure)
        return closed

    # ----- CLI verbs (HATS-1036 R5) ------------------------------------------

    def verbs(self) -> list[click.Command]:
        """The group verbs this extension contributes (ADR-0017 §4)."""
        return [_append_verdict_command(), _autoclose_command()]


def _synthetic_entry(closure: QuorumClosure, *, now: str | None) -> dict[str, Any]:
    stamp = now or utc_now()
    return {
        "date": stamp[:10],
        "verdict": "refuted",
        "evidence": (
            f"auto-closed: quorum K={closure.k} reached — independent refuted "
            f"sessions: {', '.join(closure.refute_sessions)}"
        ),
        "recommendation": "close_refuted",
        "session_id": AUTO_SESSION_ID,
        "timestamp": stamp,
    }


class PropVotesExtension:
    """Owns ``votes``; appends one vote atomically via ``Delta.fields``."""

    name = "prop-votes"

    def __init__(self) -> None:
        self._kernel: Any = None

    def subscriptions(self):
        return ()

    def on_event(self, ctx):
        return None

    def bind(self, kernel: Any) -> None:
        self._kernel = kernel

    def add_vote(self, prop_id: str, vote: Mapping[str, Any], *, actor: str, caller_cwd: Path):
        """Append one vote entry atomically (io.add_vote parity)."""
        return self._kernel.transition_ops(
            prop_id,
            [FieldsOp({"votes": Append(dict(vote))})],
            actor=actor,
            caller_cwd=caller_cwd,
        )

    def verbs(self) -> list[click.Command]:
        """The group verbs this extension contributes (ADR-0017 §4)."""
        return [_vote_command()]


# ----- CLI verb plumbing (HATS-1036 R5) --------------------------------------
# verbs() commands re-resolve the workspace at RUN time and fetch the bound
# extension by name (the composition-time instance carries no kernel).


def _ambient_session() -> str:
    return os.environ.get(ENV_SESSION_ID, "")


def _run_extension_verb(
    ext_name: str, tasks_dir: Path | None, as_json: bool, call: Callable[[Any, Path], Any]
) -> tuple[Any, bool]:
    """Resolve the bound extension declaring ``ext_name`` and run ``call(ext,
    caller_cwd)``; a raise is routed to the typed CLI surface. Returns
    ``(result, ok)``."""
    from ..workspace import Workspace  # deferred: workspace pulls the composition root

    caller_cwd = Path.cwd()
    try:
        root = resolved_root(tasks_dir, caller_cwd)
        ext = Workspace.discover([root]).extension(ext_name)
        return call(ext, caller_cwd), True
    except Exception as exc:  # noqa: BLE001 — routed to typed handling
        handle_rack_error(exc, as_json)
        return None, False


def _emit_kernel_result(result: Any, as_json: bool, headline: str) -> None:
    from ..cli_kernel import _result_payload  # deferred: cli_kernel pulls extensions

    if as_json:
        emit_json(_result_payload(result))
    else:
        click.echo(headline)


def _append_verdict_command() -> click.Command:
    @click.command("append-verdict", help="Append a validation_log verdict (atomic, no edge).")
    @click.argument("hyp_id")
    @click.option("--verdict", default=None, help="confirmed | refuted | inconclusive | n/a.")
    @click.option("--evidence", default=None, help="Non-empty evidence string (required).")
    @click.option("--recommendation", default=None, help="close_confirmed|close_refuted|keep|extend_window.")
    @click.option("--date", default=None, help="Entry date (default: today, UTC).")
    @click.option("--session-id", "session_id", default=None, help="Session id (default: ambient).")
    @click.option("--entry", "entry_json", default=None, help="Full entry as JSON (named options win).")
    @TASKS_DIR_OPT
    @JSON_OPT
    def _append_verdict(hyp_id, verdict, evidence, recommendation, date, session_id, entry_json, tasks_dir, as_json):
        try:
            entry = _verdict_entry(verdict, evidence, recommendation, date, session_id, entry_json)
        except ValueError as exc:
            handle_rack_error(exc, as_json)
            return
        result, ok = _run_extension_verb(
            "hyp-verdicts", tasks_dir, as_json,
            lambda ext, cwd: ext.append_verdict(hyp_id, entry, actor=actor(), caller_cwd=cwd),
        )
        if ok:
            _emit_kernel_result(result, as_json, f"Verdict on {hyp_id}: {entry.get('verdict')}")

    return _append_verdict


def _verdict_entry(verdict, evidence, recommendation, date, session_id, entry_json) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if entry_json:
        loaded = json.loads(entry_json)
        if not isinstance(loaded, dict):
            raise ValueError("--entry must be a JSON object")
        entry.update(loaded)
    if verdict is not None:
        entry["verdict"] = verdict
    if evidence is not None:
        entry["evidence"] = evidence
    if recommendation is not None:
        entry["recommendation"] = recommendation
    entry["date"] = date or entry.get("date") or utc_now()[:10]
    sid = session_id or entry.get("session_id") or _ambient_session()
    if sid:
        entry["session_id"] = sid
    entry.setdefault("timestamp", utc_now())
    return entry


def _autoclose_command() -> click.Command:
    @click.command("autoclose", help="Close active HYPs that reached the refuted-verdict quorum.")
    @click.option("--k", "k", default=DEFAULT_QUORUM_K, show_default=True, type=int,
                  help="Independent refuted sessions required.")
    @click.option("--dry-run", "dry_run", is_flag=True, help="Report closures without writing.")
    @TASKS_DIR_OPT
    @JSON_OPT
    def _autoclose(k, dry_run, tasks_dir, as_json):
        closures, ok = _run_extension_verb(
            "hyp-verdicts", tasks_dir, as_json,
            lambda ext, cwd: ext.autoclose(caller_cwd=cwd, k=k, dry_run=dry_run),
        )
        if not ok:
            return
        if as_json:
            emit_json({
                "dry_run": dry_run,
                "closures": [
                    {"hyp_id": c.hyp_id, "refute_sessions": list(c.refute_sessions), "k": c.k}
                    for c in closures
                ],
            })
        else:
            verb = "Would close" if dry_run else "Closed"
            if not closures:
                click.echo("No hypotheses reached quorum.")
            for c in closures:
                click.echo(f"{verb}: {c.hyp_id} (K={c.k}; sessions: {', '.join(c.refute_sessions)})")

    return _autoclose


def _vote_command() -> click.Command:
    @click.command("vote", help="Append a vote to a proposal (atomic, no edge).")
    @click.argument("prop_id")
    @click.option("--reasoning", default=None, help="Non-empty reasoning string (required).")
    @click.option("--session-id", "session_id", default=None, help="Session id (default: ambient).")
    @click.option("--timestamp", default=None, help="Vote timestamp (default: now, UTC).")
    @click.option("--entry", "entry_json", default=None, help="Full vote as JSON (named options win).")
    @TASKS_DIR_OPT
    @JSON_OPT
    def _vote(prop_id, reasoning, session_id, timestamp, entry_json, tasks_dir, as_json):
        try:
            vote = _vote_entry(reasoning, session_id, timestamp, entry_json)
        except ValueError as exc:
            handle_rack_error(exc, as_json)
            return
        result, ok = _run_extension_verb(
            "prop-votes", tasks_dir, as_json,
            lambda ext, cwd: ext.add_vote(prop_id, vote, actor=actor(), caller_cwd=cwd),
        )
        if ok:
            _emit_kernel_result(result, as_json, f"Vote on {prop_id}: {vote.get('session_id')}")

    return _vote


def _vote_entry(reasoning, session_id, timestamp, entry_json) -> dict[str, Any]:
    vote: dict[str, Any] = {}
    if entry_json:
        loaded = json.loads(entry_json)
        if not isinstance(loaded, dict):
            raise ValueError("--entry must be a JSON object")
        vote.update(loaded)
    if reasoning is not None:
        vote["reasoning"] = reasoning
    sid = session_id or vote.get("session_id") or _ambient_session()
    if sid:
        vote["session_id"] = sid
    vote["timestamp"] = timestamp or vote.get("timestamp") or utc_now()
    return vote
