"""HATS-948 (T15) — providers carry the TranscriptParser; the seam injects it.

The parser rides the ``Provider`` set (no separate registry): Claude → structured
``ClaudeParser``; every other surface defaults to the trace-only fallback. The
compose seam injects ``partial(AuditWriter, parser=provider.transcript_parser())``.
RED-under-revert: reverting ``ClaudeProvider`` to the default, or dropping the
seam's ``partial(parser=...)``, fails the tests below.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_hats.composition_seam import build_composition_payload
from ai_hats.providers import ClaudeProvider
from ai_hats_agy.provider import AgyProvider
from ai_hats_observe.parsers.claude import ClaudeParser
from ai_hats_observe.parsers.trace import TraceParser


def test_claude_provider_uses_claude_parser() -> None:
    assert isinstance(ClaudeProvider().transcript_parser(), ClaudeParser)


def test_agy_provider_uses_trace_only_parser() -> None:
    parser = AgyProvider().transcript_parser()
    assert isinstance(parser, TraceParser)
    assert not isinstance(parser, ClaudeParser)


def test_seam_injects_provider_parser(tmp_path: Path) -> None:
    sentinel = object()
    provider = MagicMock(name="provider")
    provider.transcript_parser.return_value = sentinel
    asm = MagicMock(name="assembler")
    asm.resolver.list_components.return_value = ["judge"]
    with patch("ai_hats.assembler.Assembler", return_value=asm), \
         patch("ai_hats.materialize.compose_for_role",
               return_value=MagicMock(errors=[], merged_injection="P")), \
         patch("ai_hats.providers.get_provider", return_value=provider):
        payload = build_composition_payload(tmp_path, role_override="judge")

    writer = payload.audit_writer_factory()
    assert writer.parser is sentinel
