"""Multi-turn sub-agent API — Phase 3 of HATS-474.

Production primitive that e2e tests reuse. The shape:

.. code:: python

   async with runner.session(role="maintainer") as s:
       r1 = await s.send("Analyze auth.py")
       r2 = await s.send("Now refactor to use JWT")

Each ``send`` is one user turn: the message goes to the SDK, the
response is drained until the SDK emits a terminal ``ResultMessage``,
and a :class:`Response` is returned. Across ``send`` calls the
``ClaudeSDKClient`` stays alive, so the agent keeps full conversation
history without us having to manage ``--resume`` plumbing — that's the
core win versus the legacy subprocess-per-turn shape we considered for
HATS-473 PoC-2.

Lifecycle:

* ``__aenter__``: compose role, init audit, open
  :class:`WorktreeManager`, open :class:`ClaudeSDKClient`, return a
  :class:`SubAgentSession` bound to that client.
* ``send``: drain one turn via :func:`sdk_runner.drain_one_turn`,
  accumulate per-turn transcript / cost / num_turns into the session
  aggregator, return :class:`Response`.
* ``__aexit__``: write aggregated ``transcript.txt`` / ``reasoning.log``,
  emit ``metrics.json`` with summed cost + total turns +
  ``claude_session_id`` (stable across turns) + ``stop_reason`` of the
  last turn, then drop the per-session cache dir.

Per ADR-0005 ``Composition & Pipeline Value Contract``: the runner is
the only place we couple composition + SDK options + audit. Tests can
stub :class:`claude_agent_sdk.ClaudeSDKClient` without touching this
module's contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .observe import Session


# ---------------------------------------------------------------------------
# Response — one-turn outcome surfaced to the caller of ``send``
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    """Outcome of a single :meth:`SubAgentSession.send` call.

    ``text`` is the formatted user-visible answer (mirrors the legacy
    ``transcript.txt`` shape); ``reasoning`` is the diagnostic log shape.
    Raw SDK ``messages`` are kept as a tuple for advanced consumers
    (assertion helpers, debugging) without forcing them to re-parse
    formatted strings.
    """

    text: str
    reasoning: str
    cost_usd: float | None
    num_turns: int
    stop_reason: str | None
    claude_session_id: str | None
    is_error: bool
    error: str | None = None
    messages: tuple[Any, ...] = ()

    @property
    def tool_calls(self) -> tuple:
        """Tool-use blocks from this turn, derived from ``messages``.

        Returns an empty tuple when no tool was used. Lazy import of
        SDK types keeps the dataclass cheap to instantiate.
        """
        from claude_agent_sdk import AssistantMessage, ToolUseBlock

        calls: list = []
        for msg in self.messages:
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        calls.append(block)
        return tuple(calls)


# ---------------------------------------------------------------------------
# SubAgentSession — multi-turn driver
# ---------------------------------------------------------------------------


@dataclass
class _SessionAccumulator:
    """Mutable accumulators populated across ``send`` calls.

    Kept as a private dataclass so :class:`SubAgentSession` instances
    expose only intent-revealing methods / properties — the bookkeeping
    fields aren't part of the public API.
    """

    transcript_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    cost_usd_total: float = 0.0
    has_any_cost: bool = False
    num_turns_total: int = 0
    send_count: int = 0
    claude_session_id: str | None = None
    last_stop_reason: str | None = None
    any_error: bool = False
    first_error: str | None = None


class SubAgentSession:
    """Multi-turn handle returned by :meth:`SubAgentRunner.session`.

    The SDK client is owned by the surrounding async context manager;
    this class only drives ``send`` against it. Each ``send`` produces a
    :class:`Response` and updates internal accumulators so the
    surrounding context manager can write a single coherent
    ``metrics.json`` / ``transcript.txt`` / ``reasoning.log`` on exit.
    """

    def __init__(
        self,
        *,
        client,
        session: "Session",
        role: str,
        model: str,
        isolation_mode: str,
    ) -> None:
        self._client = client
        self._session = session
        self._role = role
        self._model = model
        self._isolation_mode = isolation_mode
        self._acc = _SessionAccumulator()

    # ----- public surface -----

    async def send(self, message: str) -> Response:
        """Send one user turn; drain until terminal; return the response."""
        from .sdk_runner import drain_one_turn

        self._acc.send_count += 1
        turn_index = self._acc.send_count

        result, messages = await drain_one_turn(self._client, message)

        # Accumulate into the session-wide artifacts. Turn separators in the
        # aggregated transcript make multi-turn audit logs greppable without
        # losing per-turn boundaries.
        if result.stdout:
            self._acc.transcript_parts.append(
                f"==== turn {turn_index} ====\n{result.stdout}"
            )
        if result.stderr:
            self._acc.reasoning_parts.append(
                f"==== turn {turn_index} ====\n{result.stderr}"
            )

        if result.claude_session_id:
            self._acc.claude_session_id = result.claude_session_id
        if result.total_cost_usd is not None:
            self._acc.cost_usd_total += result.total_cost_usd
            self._acc.has_any_cost = True
        if result.num_turns is not None:
            self._acc.num_turns_total += result.num_turns
        if result.stop_reason:
            self._acc.last_stop_reason = result.stop_reason

        is_error = result.exit_code != 0
        if is_error:
            self._acc.any_error = True
            if self._acc.first_error is None and result.error:
                self._acc.first_error = result.error

        return Response(
            text=result.stdout,
            reasoning=result.stderr,
            cost_usd=result.total_cost_usd,
            num_turns=result.num_turns or 0,
            stop_reason=result.stop_reason,
            claude_session_id=result.claude_session_id,
            is_error=is_error,
            error=result.error,
            messages=tuple(messages),
        )

    # ----- accessors consumed by the surrounding async ctx manager -----

    @property
    def session_id(self) -> str:
        """The ai-hats session id (date-prefixed) — stable across turns."""
        return self._session.session_id

    @property
    def claude_session_id(self) -> str | None:
        """The SDK / Claude session id, captured on the first turn."""
        return self._acc.claude_session_id

    @property
    def total_cost_usd(self) -> float | None:
        """Sum of ``ResultMessage.total_cost_usd`` across turns; ``None``
        when no turn ever surfaced a cost (typical for network-error
        sessions where the SDK could not compute one).
        """
        return self._acc.cost_usd_total if self._acc.has_any_cost else None

    @property
    def num_turns_total(self) -> int:
        return self._acc.num_turns_total

    @property
    def send_count(self) -> int:
        return self._acc.send_count

    @property
    def last_stop_reason(self) -> str | None:
        return self._acc.last_stop_reason

    @property
    def is_error(self) -> bool:
        """True iff ANY send within this session was an error turn."""
        return self._acc.any_error

    @property
    def first_error(self) -> str | None:
        return self._acc.first_error

    @property
    def aggregated_transcript(self) -> str:
        """Newline-separated per-turn transcripts. Empty string when no turn ran."""
        return "\n\n".join(self._acc.transcript_parts)

    @property
    def aggregated_reasoning(self) -> str:
        return "\n\n".join(self._acc.reasoning_parts)
