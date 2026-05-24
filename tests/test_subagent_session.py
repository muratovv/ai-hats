"""Unit tests for HATS-474 Phase 3 multi-turn API.

Covers two layers:

1. :class:`SubAgentSession` in isolation — drive a single instance with
   a stub SDK client through one or more ``send`` calls; assert that
   the per-turn :class:`Response` and the cross-turn accumulator
   (transcript / reasoning / cost / num_turns / claude_session_id /
   stop_reason / is_error) move the way the audit step depends on.

2. :meth:`SubAgentRunner.session` end-to-end — open the async context
   manager against a minimal project + monkey-patched
   ``ClaudeSDKClient``, exercise multi-turn ``send`` from inside the
   ``async with``, then verify that the surrounding lifecycle
   (``init_audit``, ``finalize_audit``, cache cleanup) produced a
   coherent ``metrics.json`` with the new ``num_turns_total`` /
   ``send_count`` / summed ``total_cost_usd`` fields.

The bundled ``claude`` binary is never spawned in this file. Real-SDK
coverage is in ``tests/e2e/test_subagent_session_multi_turn.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.observe import Session, SessionManager
from ai_hats.subagent_session import Response, SubAgentSession


# ---------------------------------------------------------------------------
# Message constructors
# ---------------------------------------------------------------------------


def _assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model="claude-haiku-4-5",
        parent_tool_use_id=None,
        error=None,
        usage=None,
        message_id=None,
        stop_reason=None,
        session_id=None,
        uuid=None,
    )


def _result(
    *,
    subtype: str = "success",
    is_error: bool = False,
    session_id: str = "sdk-sid-stable",
    cost: float | None = 0.0010,
    turns: int = 1,
    stop_reason: str | None = "end_turn",
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=100,
        duration_api_ms=80,
        is_error=is_error,
        num_turns=turns,
        session_id=session_id,
        stop_reason=stop_reason,
        total_cost_usd=cost,
        usage=None,
        result=None,
        structured_output=None,
        model_usage=None,
        permission_denials=None,
        deferred_tool_use=None,
        errors=None,
        api_error_status=None,
        uuid=None,
    )


# ---------------------------------------------------------------------------
# Stub SDK client — open async ctx + canned per-turn message sequences
# ---------------------------------------------------------------------------


class _StubClient:
    """Multi-turn-aware stand-in for ``ClaudeSDKClient``.

    Each ``query(msg)`` advances an internal turn index; the next call
    to ``receive_response()`` yields the message sequence for that turn.
    Tests pass a list-of-lists (one inner list per planned send) plus
    optional failure injection per turn.
    """

    def __init__(self, *, turn_responses, raise_on_query=None,
                 raise_during_stream=None):
        self._turn_responses = list(turn_responses)
        self._cur = 0
        self.received_queries: list[str] = []
        self._raise_on_query = raise_on_query
        self._raise_during_stream = raise_during_stream
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return False

    async def query(self, msg: str):
        self.received_queries.append(msg)
        if self._raise_on_query is not None:
            raise self._raise_on_query

    async def receive_response(self):
        idx = self._cur
        self._cur += 1
        if idx >= len(self._turn_responses):
            raise IndexError(f"_StubClient: no canned response for turn {idx}")
        for m in self._turn_responses[idx]:
            if self._raise_during_stream is not None:
                raise self._raise_during_stream
            yield m


def _bare_session(tmp_path: Path) -> Session:
    """A real ai-hats Session with an initialised audit — minimal fixture."""
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    s = Session(session_id="test-sid", session_dir=session_dir)
    s.init_audit(role="role", provider="claude", model="")
    return s


# ===========================================================================
# Layer 1: SubAgentSession in isolation
# ===========================================================================


def _async(coro):
    """Run an async test body to completion — keeps tests sync-typed so
    we don't depend on the optional ``pytest-asyncio`` plugin."""
    return asyncio.run(coro)


class TestSubAgentSession:
    """``send`` accumulator + Response shape."""

    def test_single_send_response_shape(self, tmp_path):
        client = _StubClient(turn_responses=[[
            _assistant(TextBlock(text="hello")),
            _result(session_id="sdk-1", cost=0.0021, turns=1),
        ]])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )

        async def _t():
            async with client:
                return await sub.send("hi")

        r = _async(_t())
        assert isinstance(r, Response)
        assert r.text == "hello\n"
        assert r.is_error is False
        assert r.cost_usd == 0.0021
        assert r.num_turns == 1
        assert r.stop_reason == "end_turn"
        assert r.claude_session_id == "sdk-1"
        assert client.received_queries == ["hi"]

    def test_two_sends_aggregate(self, tmp_path):
        client = _StubClient(turn_responses=[
            [_assistant(TextBlock(text="A")), _result(cost=0.001, turns=1)],
            [_assistant(TextBlock(text="B")), _result(cost=0.002, turns=2)],
        ])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )

        async def _t():
            async with client:
                await sub.send("first")
                await sub.send("second")

        _async(_t())
        # send_count is independent of num_turns_total: send_count == 2
        # because we called .send twice; num_turns_total == 1+2 == 3 because
        # the SDK accumulates inner turns per send.
        assert sub.send_count == 2
        assert sub.num_turns_total == 3
        # Cost summed across sends.
        assert sub.total_cost_usd == pytest.approx(0.003)
        # Stop reason is the last turn's.
        assert sub.last_stop_reason == "end_turn"
        # No errors recorded.
        assert sub.is_error is False
        assert sub.first_error is None
        # Both queries reached the client in order.
        assert client.received_queries == ["first", "second"]

    def test_first_send_captures_claude_session_id(self, tmp_path):
        client = _StubClient(turn_responses=[
            [_assistant(TextBlock(text="a")), _result(session_id="claude-uuid-X")],
            [_assistant(TextBlock(text="b")), _result(session_id="claude-uuid-X")],
        ])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )
        captured = []

        async def _t():
            async with client:
                await sub.send("first")
                captured.append(sub.claude_session_id)
                await sub.send("second")
                captured.append(sub.claude_session_id)

        _async(_t())
        assert captured == ["claude-uuid-X", "claude-uuid-X"]

    def test_aggregated_transcript_has_turn_markers(self, tmp_path):
        client = _StubClient(turn_responses=[
            [_assistant(TextBlock(text="alpha")), _result()],
            [_assistant(TextBlock(text="beta")), _result()],
        ])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )

        async def _t():
            async with client:
                await sub.send("1")
                await sub.send("2")

        _async(_t())
        out = sub.aggregated_transcript
        assert "==== turn 1 ====" in out
        assert "==== turn 2 ====" in out
        assert "alpha" in out
        assert "beta" in out
        assert out.index("turn 1") < out.index("turn 2")

    def test_aggregated_reasoning_has_turn_markers(self, tmp_path):
        client = _StubClient(turn_responses=[
            [
                SystemMessage(subtype="init", data={"model": "claude-haiku-4-5"}),
                _assistant(TextBlock(text="x")),
                _result(),
            ],
        ])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )

        async def _t():
            async with client:
                await sub.send("hello")

        _async(_t())
        reasoning = sub.aggregated_reasoning
        assert "==== turn 1 ====" in reasoning
        assert "[system:init]" in reasoning

    def test_send_error_marks_session(self, tmp_path):
        """A turn whose ResultMessage.is_error → Response.is_error and
        SubAgentSession.is_error both surface; first_error captured."""
        client = _StubClient(turn_responses=[
            [
                _assistant(TextBlock(text="oops")),
                _result(is_error=True, subtype="error_max_turns"),
            ],
        ])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )

        async def _t():
            async with client:
                return await sub.send("ask")

        r = _async(_t())
        assert r.is_error is True
        assert sub.is_error is True
        # The ResultMessage didn't carry a textual error — that's expected
        # for error_max_turns; first_error stays None until an actual
        # exception or stream-end-without-result path runs.
        assert sub.first_error is None

    def test_send_stream_end_without_result_is_error(self, tmp_path):
        """Drain helper returns an error envelope when the stream ends
        without a terminal ResultMessage; the session must surface this
        in first_error so finalize_audit can record it."""
        client = _StubClient(turn_responses=[
            [_assistant(TextBlock(text="incomplete"))],  # no ResultMessage
        ])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )

        async def _t():
            async with client:
                return await sub.send("ask")

        r = _async(_t())
        assert r.is_error is True
        assert sub.is_error is True
        assert sub.first_error is not None
        assert "without ResultMessage" in sub.first_error

    def test_no_cost_when_no_turn_surfaced_one(self, tmp_path):
        """``total_cost_usd`` is None when no ResultMessage carried cost."""
        client = _StubClient(turn_responses=[
            [_assistant(TextBlock(text="x")), _result(cost=None)],
        ])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )

        async def _t():
            async with client:
                await sub.send("ask")

        _async(_t())
        assert sub.total_cost_usd is None

    def test_concurrent_sends_are_serialised(self, tmp_path):
        """Two coroutines awaiting ``send`` in parallel must NOT interleave.

        Regression for the HATS-474 review finding: ``client.query`` +
        ``client.receive_response`` share one bidirectional channel, so
        racing two ``send`` calls would scramble queries with
        responses. The session-internal lock serialises them; both turns
        complete cleanly and ``send_count`` is exactly 2.

        We sequence the stub to record the order ``query`` arrived in;
        no matter how the asyncio scheduler interleaves the wrapping
        ``send`` calls, the lock keeps each ``query`` → drain pair
        atomic, so ``received_queries`` lists both prompts in caller
        order without intermixing.
        """
        client = _StubClient(turn_responses=[
            [_assistant(TextBlock(text="A")), _result(cost=0.001)],
            [_assistant(TextBlock(text="B")), _result(cost=0.002)],
        ])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )

        async def _t():
            async with client:
                # asyncio.gather launches both coroutines concurrently;
                # the lock must serialise them.
                results = await asyncio.gather(
                    sub.send("first"),
                    sub.send("second"),
                )
                return results

        results = _async(_t())
        assert len(results) == 2
        # Both turns succeeded; the per-turn cost reached the response.
        assert {results[0].cost_usd, results[1].cost_usd} == {0.001, 0.002}
        # Aggregator records both sends — not partial / not 3.
        assert sub.send_count == 2
        # Both queries reached the client; order is whichever the
        # scheduler picked, but neither was lost.
        assert sorted(client.received_queries) == ["first", "second"]

    def test_response_tool_calls_derived_from_messages(self, tmp_path):
        client = _StubClient(turn_responses=[[
            _assistant(
                TextBlock(text="Looking up..."),
                ToolUseBlock(id="t1", name="Read", input={"path": "x.py"}),
                ToolUseBlock(id="t2", name="Grep", input={"pattern": "foo"}),
            ),
            _result(),
        ]])
        sub = SubAgentSession(
            client=client, session=_bare_session(tmp_path),
            role="role", model="", isolation_mode="discard",
        )

        async def _t():
            async with client:
                return await sub.send("ask")

        r = _async(_t())
        calls = r.tool_calls
        assert len(calls) == 2
        assert calls[0].name == "Read"
        assert calls[1].name == "Grep"


# ===========================================================================
# Layer 2: SubAgentRunner.session() end-to-end (stubbed SDK)
# ===========================================================================


@pytest.fixture
def minimal_project(tmp_path: Path) -> Path:
    """Project + minimal library wired for SubAgentRunner.session()."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"
    role_dir = lib / "roles" / "probe"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(
        "name: probe\n"
        "priorities: [Quality]\n"
        "composition:\n  traits: []\n  rules: []\n  skills: []\n"
        "injection: Probe role.\n"
    )
    ProjectConfig(provider="claude", library_paths=[str(lib)]).save(
        project / "ai-hats.yaml"
    )
    asm = Assembler(project)
    asm.init()
    asm.set_role("probe", provider_name="claude")
    return project


class TestSubAgentRunnerSession:
    def test_session_wrong_provider_raises(self, minimal_project, monkeypatch):
        """Multi-turn API is Claude-only; misuse fails fast with a clear error."""
        from ai_hats.runtime import SubAgentRunner

        # Flip provider after init to simulate misuse.
        cfg_path = minimal_project / "ai-hats.yaml"
        text = cfg_path.read_text().replace("provider: claude", "provider: gemini")
        cfg_path.write_text(text)

        runner = SubAgentRunner(minimal_project)
        with pytest.raises(ValueError, match="Claude-only"):
            runner.session("probe")

    def test_session_end_to_end_writes_aggregated_metrics(
        self, minimal_project, monkeypatch
    ):
        """Two-turn session writes one coherent metrics.json + transcript.txt."""
        from ai_hats import runtime as runtime_mod
        from ai_hats.runtime import SubAgentRunner

        # Stub the SDK client so two sends each get a canned response.
        stub_holder: dict = {}

        def _factory(options):
            client = _StubClient(turn_responses=[
                [_assistant(TextBlock(text="answer-A")),
                 _result(session_id="sdk-multi", cost=0.0010, turns=1)],
                [_assistant(TextBlock(text="answer-B")),
                 _result(session_id="sdk-multi", cost=0.0020, turns=2)],
            ])
            stub_holder["client"] = client
            stub_holder["options"] = options
            return client

        monkeypatch.setattr(
            "claude_agent_sdk.ClaudeSDKClient", _factory,
        )
        # Stub cleanup so we can inspect transcript before it disappears.
        monkeypatch.setattr(
            runtime_mod, "_cleanup_session_cache", lambda *a, **kw: None,
        )

        runner = SubAgentRunner(minimal_project)

        captured: dict = {}

        async def _drive():
            async with runner.session("probe", model="claude-haiku-4-5") as s:
                r1 = await s.send("first")
                r2 = await s.send("second")
                captured["r1"] = r1
                captured["r2"] = r2
                captured["session_id"] = s.session_id

        asyncio.run(_drive())

        assert captured["r1"].text == "answer-A\n"
        assert captured["r2"].text == "answer-B\n"

        # The SDK options received by ClaudeSDKClient must carry our
        # composition — verify by checking the model passthrough.
        assert stub_holder["options"].model == "claude-haiku-4-5"

        # Locate the session dir via SessionManager.
        sess_dir = SessionManager(minimal_project).get_session(
            captured["session_id"],
        ).session_dir
        assert sess_dir.is_dir(), "session dir not found"

        metrics = json.loads((sess_dir / "metrics.json").read_text())
        assert metrics["exit_code"] == 0
        assert metrics["claude_session_id"] == "sdk-multi"
        # Cost summed across both turns.
        assert metrics["total_cost_usd"] == pytest.approx(0.0030)
        # num_turns from each result: 1 + 2 = 3.
        assert metrics["num_turns_total"] == 3
        assert metrics["send_count"] == 2
        assert metrics["stop_reason"] == "end_turn"

        transcript = (sess_dir / "transcript.txt").read_text()
        assert "==== turn 1 ====" in transcript
        assert "==== turn 2 ====" in transcript
        assert "answer-A" in transcript
        assert "answer-B" in transcript

    def test_session_caller_exception_propagates_and_records(
        self, minimal_project, monkeypatch
    ):
        """User code inside ``async with runner.session(...)`` raises →
        the exception propagates to the caller AND metrics.json marks
        the session as errored."""
        from ai_hats import runtime as runtime_mod
        from ai_hats.runtime import SubAgentRunner

        def _factory(options):
            return _StubClient(turn_responses=[
                [_assistant(TextBlock(text="ok")), _result(session_id="sdk-x", cost=0.0001)],
            ])

        monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", _factory)
        monkeypatch.setattr(
            runtime_mod, "_cleanup_session_cache", lambda *a, **kw: None,
        )

        runner = SubAgentRunner(minimal_project)
        captured_sid: dict = {}

        async def _drive():
            async with runner.session("probe") as s:
                await s.send("hi")
                captured_sid["sid"] = s.session_id
                raise RuntimeError("caller blew up")

        with pytest.raises(RuntimeError, match="caller blew up"):
            asyncio.run(_drive())

        sess_dir = SessionManager(minimal_project).get_session(
            captured_sid["sid"]
        ).session_dir
        assert sess_dir.is_dir()
        m = json.loads((sess_dir / "metrics.json").read_text())
        assert m["exit_code"] != 0
        assert "caller blew up" in m["error"]
        # The successful turn before the raise still contributed to metrics.
        assert m["send_count"] == 1
        assert m["total_cost_usd"] == pytest.approx(0.0001)

    def test_session_finalize_runs_even_when_first_send_errors(
        self, minimal_project, monkeypatch
    ):
        """SDK error on first send → session marked errored, finalize runs."""
        from ai_hats import runtime as runtime_mod
        from ai_hats.runtime import SubAgentRunner

        def _factory(options):
            return _StubClient(
                turn_responses=[[]],  # empty stream → "stream ended without ResultMessage"
            )

        monkeypatch.setattr("claude_agent_sdk.ClaudeSDKClient", _factory)
        monkeypatch.setattr(
            runtime_mod, "_cleanup_session_cache", lambda *a, **kw: None,
        )

        runner = SubAgentRunner(minimal_project)
        captured_sid: dict = {}

        async def _drive():
            async with runner.session("probe") as s:
                await s.send("hi")
                captured_sid["sid"] = s.session_id

        asyncio.run(_drive())  # should NOT raise — error captured in metrics

        sess_dir = SessionManager(minimal_project).get_session(
            captured_sid["sid"]
        ).session_dir
        assert sess_dir.is_dir()
        m = json.loads((sess_dir / "metrics.json").read_text())
        assert m["exit_code"] != 0
        assert "without ResultMessage" in m["error"]
