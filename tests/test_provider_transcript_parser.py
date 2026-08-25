"""HATS-948 (T15) — providers carry the TranscriptParser; the seam injects it.

The parser rides the ``Provider`` set (no separate registry): Claude → structured
``ClaudeParser``; agy → ``AgyParser`` (HATS-1391), which keeps the trace-only
fallback inside itself; a surface that declares nothing still gets ``TraceParser``.
The compose seam injects ``partial(AuditWriter, parser=provider.transcript_parser())``.
RED-under-revert: reverting ``ClaudeProvider`` or ``AgyProvider`` to the default,
or dropping the seam's ``partial(parser=...)``, fails the tests below.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_hats.composition_seam import build_composition_payload
from ai_hats.surfaces.claude.provider import ClaudeProvider
from ai_hats.surfaces.agy.parser import AgyParser
from ai_hats.surfaces.agy.provider import AgyProvider
from ai_hats_observe.parsers.claude import ClaudeParser
from ai_hats_observe.parsers.trace import TraceParser


def test_claude_provider_uses_claude_parser() -> None:
    assert isinstance(ClaudeProvider().transcript_parser(), ClaudeParser)


def test_agy_provider_uses_agy_parser() -> None:
    parser = AgyProvider().transcript_parser()
    assert isinstance(parser, AgyParser)
    assert not isinstance(parser, ClaudeParser)


def test_agy_parser_falls_back_to_trace_without_jsonl(tmp_path: Path) -> None:
    """The trace-only property the agy surface used to get from ``TraceParser`` itself."""
    trace = tmp_path / "trace.log"
    trace.write_text(
        "12:00:00.000 [REQ] check the plan\n"
        "12:00:01.000 [RES] ⏺ Read(plan.md)\n"
        "12:00:02.000 [RES] ⏺ Done.\n"
    )

    parsed = AgyParser().parse(None, trace)
    expected = TraceParser().parse(None, trace)

    assert [(t.user_input, t.tools, t.response) for t in parsed.turns] == [
        (t.user_input, t.tools, t.response) for t in expected.turns
    ]
    # HATS-1433: the trace text yields an estimated count, and usage.json now says
    # so — it used to carry the number with no provenance flag at all, which is
    # what let it contradict metrics.json for the same session.
    assert AgyParser().parse_usage(None, trace)["flags"] == [
        "no-structured-transcript",
        "token-telemetry-estimated",
    ]


def test_seam_injects_provider_parser(tmp_path: Path) -> None:
    sentinel = object()
    provider = MagicMock(name="provider")
    provider.transcript_parser.return_value = sentinel
    asm = MagicMock(name="assembler")
    asm.resolver.list_components.return_value = ["judge"]
    with (
        patch("ai_hats.assembler.Assembler", return_value=asm),
        patch(
            "ai_hats.materialize.compose_for_role",
            return_value=MagicMock(errors=[], merged_injection="P"),
        ),
        patch("ai_hats.providers.get_provider", return_value=provider),
    ):
        payload = build_composition_payload(tmp_path, role_override="judge")

    writer = payload.audit_writer_factory()
    assert writer.parser is sentinel
