"""LiveSession — multi-turn agent driver for e2e tests (HATS-474 Phase 4).

Thin async-context wrapper over
:meth:`ai_hats.runtime.SubAgentRunner.session` that adds chainable
assertion verbs and budget-cap conveniences. The underlying multi-turn
machinery — composition, SDK client lifecycle, cost aggregation,
``transcript.txt`` / ``reasoning.log`` / ``metrics.json`` writes — is
production code shipped in Phases 2 and 3; this module adds **zero**
new mechanism, just an ergonomic test surface.

Usage::

    async with live_session(project_dir, role="probe") as s:
        r = (await s.send("ping"))
        r.expect_no_error().expect_contains("pong")

        await s.send("again")
        s.expect_send_count(2).expect_cost_under(0.10)

Replaces the W0-pilot ``LiveSession`` that used PTY + idle-detection
against the bare ``ai-hats`` HITL session. That approach hit a hard
wall (HATS-473 PoC): the claude TUI emits a continuous ANSI stream
(cursor blink, status bar), so "idle for 1.5 s" never fires. The SDK
gives us reliable per-turn ``ResultMessage`` sentinels — no more
guesswork.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats.runtime import SubAgentRunner

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ai_hats.subagent_session import Response, SubAgentSession


# ---------------------------------------------------------------------------
# Response wrapper — chainable expect_* verbs around production Response
# ---------------------------------------------------------------------------


class TurnResult:
    """Wraps a single-turn :class:`Response` with chainable assertions.

    All ``expect_*`` methods return ``self`` so tests can compose them
    into a single fluent statement, e.g.::

        (await s.send("...")).expect_no_error().expect_contains("OK")
    """

    def __init__(self, response: "Response") -> None:
        self._r = response

    # ----- accessors -----

    @property
    def response(self) -> "Response":
        return self._r

    @property
    def text(self) -> str:
        return self._r.text

    @property
    def is_error(self) -> bool:
        return self._r.is_error

    @property
    def cost_usd(self) -> float | None:
        return self._r.cost_usd

    @property
    def num_turns(self) -> int:
        return self._r.num_turns

    @property
    def tool_calls(self) -> tuple:
        return self._r.tool_calls

    # ----- error state -----

    def expect_no_error(self) -> "TurnResult":
        """Turn must have finished with ``is_error=False``."""
        if self._r.is_error:
            raise AssertionError(
                "turn errored: "
                f"{self._r.error or '<no error message>'}\n"
                f"--- response text tail (500 chars) ---\n"
                f"{self._r.text[-500:]}"
            )
        return self

    def expect_error(self) -> "TurnResult":
        """Turn must have finished with ``is_error=True`` (inverse of
        :meth:`expect_no_error`; useful for failure-mode tests)."""
        if not self._r.is_error:
            raise AssertionError(
                "expected turn to error but it succeeded.\n"
                f"--- response text tail (500 chars) ---\n"
                f"{self._r.text[-500:]}"
            )
        return self

    # ----- text content -----

    def expect_contains(self, *markers: str) -> "TurnResult":
        """All ``markers`` (case-sensitive) must appear in ``text``."""
        missing = [m for m in markers if m not in self._r.text]
        if missing:
            raise AssertionError(
                f"response missing markers: {missing}\n"
                f"--- response text tail (500 chars) ---\n"
                f"{self._r.text[-500:]}"
            )
        return self

    def expect_contains_ci(self, *markers: str) -> "TurnResult":
        """Case-insensitive variant of :meth:`expect_contains` — handy
        when the agent's wording is unpredictable but a keyword is."""
        haystack = self._r.text.lower()
        missing = [m for m in markers if m.lower() not in haystack]
        if missing:
            raise AssertionError(
                f"response missing markers (ci): {missing}\n"
                f"--- response text tail (500 chars) ---\n"
                f"{self._r.text[-500:]}"
            )
        return self

    def expect_omits(self, *markers: str) -> "TurnResult":
        """``markers`` MUST NOT appear in ``text``."""
        leaked = [m for m in markers if m in self._r.text]
        if leaked:
            raise AssertionError(
                f"response should NOT contain markers but does: {leaked}\n"
                f"--- response text tail (500 chars) ---\n"
                f"{self._r.text[-500:]}"
            )
        return self

    # ----- tool use -----

    def expect_tool_used(self, name: str) -> "TurnResult":
        """At least one ``ToolUseBlock`` in this turn must have ``name``."""
        used = [c.name for c in self._r.tool_calls]
        if name not in used:
            raise AssertionError(
                f"expected tool {name!r} to be used; tools used this turn: "
                f"{used or '<none>'}"
            )
        return self

    def expect_no_tools(self) -> "TurnResult":
        """No tool calls should have been made this turn."""
        used = [c.name for c in self._r.tool_calls]
        if used:
            raise AssertionError(
                f"expected no tool calls but agent used: {used}"
            )
        return self

    # ----- per-turn cost -----

    def expect_cost_under(self, usd: float) -> "TurnResult":
        """Per-turn cost must be tracked and below ``usd``."""
        if self._r.cost_usd is None:
            raise AssertionError(
                f"per-turn cost not tracked (None); expected < ${usd}"
            )
        if self._r.cost_usd >= usd:
            raise AssertionError(
                f"per-turn cost ${self._r.cost_usd:.4f} >= budget ${usd}"
            )
        return self


# ---------------------------------------------------------------------------
# LiveSession — the multi-turn driver
# ---------------------------------------------------------------------------


class LiveSession:
    """Multi-turn live agent session with assertion sugar.

    Constructed by :func:`live_session` — the async context manager
    that wires it up against :meth:`SubAgentRunner.session`. Each call
    to :meth:`send` returns a :class:`TurnResult` so per-turn assertions
    chain naturally. Session-wide assertions (``expect_cost_under``,
    ``expect_send_count``) read the underlying production aggregator.
    """

    def __init__(self, sub: "SubAgentSession", project_dir: Path) -> None:
        self._sub = sub
        self._project_dir = project_dir

    # ----- accessors mirroring SubAgentSession surface -----

    @property
    def session_id(self) -> str:
        return self._sub.session_id

    @property
    def claude_session_id(self) -> str | None:
        return self._sub.claude_session_id

    @property
    def total_cost_usd(self) -> float | None:
        return self._sub.total_cost_usd

    @property
    def send_count(self) -> int:
        return self._sub.send_count

    @property
    def num_turns_total(self) -> int:
        return self._sub.num_turns_total

    @property
    def aggregated_transcript(self) -> str:
        return self._sub.aggregated_transcript

    @property
    def aggregated_reasoning(self) -> str:
        return self._sub.aggregated_reasoning

    @property
    def is_error(self) -> bool:
        return self._sub.is_error

    # ----- the verb that drives the dialog -----

    async def send(self, message: str) -> TurnResult:
        """Send one user turn; return a chainable :class:`TurnResult`."""
        r = await self._sub.send(message)
        return TurnResult(r)

    # ----- session-wide assertions -----

    def expect_cost_under(self, usd: float) -> "LiveSession":
        """Aggregated cost across all turns must be tracked and below ``usd``."""
        cost = self._sub.total_cost_usd
        if cost is None:
            raise AssertionError(
                f"aggregated cost not tracked (None); expected < ${usd}"
            )
        if cost >= usd:
            raise AssertionError(
                f"aggregated cost ${cost:.4f} >= budget ${usd}"
            )
        return self

    def expect_send_count(self, n: int) -> "LiveSession":
        """Exactly ``n`` :meth:`send` calls must have completed."""
        if self._sub.send_count != n:
            raise AssertionError(
                f"expected {n} sends; got {self._sub.send_count}"
            )
        return self

    def expect_no_error(self) -> "LiveSession":
        """No turn within this session may have errored."""
        if self._sub.is_error:
            raise AssertionError(
                f"session had at least one errored turn: "
                f"{self._sub.first_error or '<no error message>'}"
            )
        return self

    def expect_claude_session_id(self) -> "LiveSession":
        """The SDK must have surfaced a ``claude_session_id`` by now —
        i.e. at least one ``ResultMessage`` reached us. Smoke check for
        the SDK round-trip itself."""
        if not self._sub.claude_session_id:
            raise AssertionError(
                "claude_session_id is None — no ResultMessage seen, "
                "the SDK round-trip may have failed before any turn "
                "completed"
            )
        return self


# ---------------------------------------------------------------------------
# Public entry point — async context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def live_session(
    project_dir: Path,
    *,
    role: str,
    model: str = "claude-haiku-4-5",
    isolation_mode: str = "discard",
    max_budget_usd: float | None = 0.50,
    max_turns: int | None = None,
    permission_mode: str | None = None,
    allowed_tools: list[str] | None = None,
    parent_session: str | None = None,
    tags: dict[str, str] | None = None,
    system_prompt_override: str | None = None,
):
    """Open a :class:`LiveSession` against ``project_dir``.

    Defaults are CI-friendly:

    * ``model="claude-haiku-4-5"`` — cheapest tier
    * ``max_budget_usd=0.50`` — hard cap enforced by the SDK; raise
      explicitly in tests that need more

    ``permission_mode`` / ``allowed_tools`` forward to the SDK so tests
    that need the agent to use specific tools without per-call
    permission prompts can pre-approve them (e.g. ``allowed_tools=
    ["Read"]`` to let the agent read files; ``permission_mode=
    "bypassPermissions"`` for sandboxed scenarios).

    All other kwargs forward to :meth:`SubAgentRunner.session`. The
    underlying ``async with`` manages composition, audit, worktree
    isolation, SDK client lifecycle, and aggregated metrics writes —
    this helper adds no new failure modes of its own.
    """
    runner = SubAgentRunner(project_dir)
    async with runner.session(
        role,
        model=model,
        isolation_mode=isolation_mode,
        max_budget_usd=max_budget_usd,
        max_turns=max_turns,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        parent_session=parent_session,
        tags=tags,
        system_prompt_override=system_prompt_override,
    ) as sub:
        yield LiveSession(sub, project_dir)
