"""Prompt assembly (ADR-0035 D5): the block a prompt lands in is the prompt's
own word, it opens where its first member appears, and the order of text is
the order of terms."""

from __future__ import annotations

from pathlib import Path

from ai_hats.diagnostics import Diagnostic
from ai_hats.processing.expr import parse_expr
from ai_hats.processing.fold import expand_and_fold
from ai_hats.processing.names import merge
from ai_hats.processing.order import program
from ai_hats.processing.prompt import assemble
from ai_hats.processing.roots import Root, read_roots
from tests._flat_library import write_root


def _prompt(block: str, text: str, heading: str | None = None) -> str:
    head = f"heading: {heading}\n" if heading else ""
    return f"---\nschema_version: 1\ntype: prompt\nblock: {block}\n{head}---\n{text}"


ADR_EXAMPLE = {
    "prompt1.md": _prompt("body", "PROMPT1"),
    "prompt2.md": _prompt("body", "PROMPT2"),
    "rule1.md": _prompt("RULES", "RULE1", heading="rule1"),
    "rule2.md": _prompt("RULES", "RULE2", heading="rule2"),
    "priority1.md": _prompt("PRIORITIES", "PRIORITY1"),
    "skill-only.yaml": "schema_version: 1\ntype: composite\nexpr: tool\n",
    "skills/tool/SKILL.md": "---\nname: tool\nai_hats:\n  schema_version: 1\n  type: skill\n---\n",
}


def _assemble(root: Path, expr: str, diagnostics: list[Diagnostic] | None = None):
    sink = diagnostics if diagnostics is not None else []
    library = read_roots([Root(root)], sink)
    tree = merge(library, sink)
    folded = expand_and_fold(program(library, tree, parse_expr(expr)), expr, sink)
    return assemble(folded, tree, sink)


def test_the_adr_example_renders_exactly(tmp_path: Path):
    root = write_root(tmp_path / "lib", ADR_EXAMPLE)
    diagnostics: list[Diagnostic] = []
    prompt = _assemble(root, "prompt1 + rule1 + priority1 + rule2 + prompt2", diagnostics)
    assert [(b.name, [m.name for m in b.members]) for b in prompt.blocks] == [
        (None, ["prompt1", "prompt2"]),
        ("RULES", ["rule1", "rule2"]),
        ("PRIORITIES", ["priority1"]),
    ]
    assert prompt.text == (
        "PROMPT1\n\nPROMPT2\n\n"
        "## RULES\n\n### rule1\nRULE1\n\n### rule2\nRULE2\n\n\n"
        "## PRIORITIES\nPRIORITY1"
    )
    assert diagnostics == []


def test_a_later_term_lands_later_and_a_block_opens_at_its_first_member(tmp_path: Path):
    root = write_root(tmp_path / "lib", ADR_EXAMPLE)
    prompt = _assemble(root, "priority1 + prompt2 + prompt1")
    assert [(b.name, [m.name for m in b.members]) for b in prompt.blocks] == [
        ("PRIORITIES", ["priority1"]),
        (None, ["prompt2", "prompt1"]),
    ]


def test_a_body_prompt_that_does_not_come_through_the_first_term_warns(tmp_path: Path):
    root = write_root(tmp_path / "lib", ADR_EXAMPLE)
    diagnostics: list[Diagnostic] = []
    prompt = _assemble(root, "skill-only + prompt1", diagnostics)
    assert prompt.blocks[0].members[0].name == "prompt1"
    assert [d.text for d in diagnostics] == [
        "the first body prompt, prompt1, comes through term 2 of the expression, not the "
        "first — the prose the agent reads first is not the first term's"
    ]
    diagnostics.clear()
    _assemble(root, "rule1 + prompt1", diagnostics)
    assert len(diagnostics) == 1, "a RULES prompt first is the same degradation"
    diagnostics.clear()
    assert _assemble(root, "skill-only + rule1", diagnostics).blocks[0].name == "RULES"
    assert diagnostics == [], "no body prompt at all is legal: a role of one skill"


def test_expansion_is_applied_to_every_member(tmp_path: Path):
    root = write_root(tmp_path / "lib", ADR_EXAMPLE)
    library = read_roots([Root(root)], [])
    tree = merge(library, [])
    folded = expand_and_fold(program(library, tree, parse_expr("prompt1 + rule1")), "x", [])
    prompt = assemble(folded, tree, [], expand=str.lower)
    assert [m.text for b in prompt.blocks for m in b.members] == ["prompt1", "rule1"]
