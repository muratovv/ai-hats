"""Parsing (ADR-0035 D1): a root is read whole and once, every entity checked
against the schema of its ``type``; what is not an entity is payload."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.diagnostics import Diagnostic, Level
from ai_hats.processing.errors import ProcessingError
from ai_hats.processing.parse import Composite, Declared, PromptEntity, SkillEntity
from ai_hats.processing.roots import Root, candidate_roots, read_roots
from tests._flat_library import SAMPLE, write_root


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return write_root(tmp_path / "lib")


def _read(root: Path, diagnostics: list[Diagnostic] | None = None):
    library = read_roots([Root(root)], diagnostics if diagnostics is not None else [])
    (layer,) = library.layers
    return {e.name: e for e in layer.entities}


def test_a_root_is_read_into_entities_named_by_their_paths(root: Path):
    entities = _read(root)
    assert set(entities) == {
        "alpha",
        "alpha::prompt",
        "alpha::priorities",
        "shared",
        "shared::prompt",
        "rules::rule-one",
        "bare",
        "bare::prompt",
        "skills::tool",
        "ops::rack.transition",
    }
    alpha = entities["alpha"]
    assert isinstance(alpha, Composite)
    assert [t.name for t in alpha.expr.terms] == [
        "alpha::prompt",
        "alpha::priorities",
        "shared",
        "tool",
    ]
    assert alpha.tags == ("role", "hitl") and alpha.summary == "the sample role"
    assert entities["shared"].tags == () and entities["shared"].summary is None

    rule = entities["rules::rule-one"]
    assert isinstance(rule, PromptEntity)
    assert (rule.block, rule.heading, rule.text) == ("RULES", "rule-one", "RULE ONE\n")
    assert entities["alpha::priorities"].text == "1. Speed", "bytes after the fence, unnormalized"

    op = entities["ops::rack.transition"]
    assert isinstance(op, Declared) and op.type == "operation"


def test_a_skill_folder_is_one_entity_and_its_tree_is_not_walked(root: Path):
    diagnostics: list[Diagnostic] = []
    entities = _read(root, diagnostics)
    tool = entities["skills::tool"]
    assert isinstance(tool, SkillEntity)
    assert tool.path == root / "skills" / "tool"
    assert tool.document.startswith("---\nname: tool")
    assert tool.on_path == ("scripts",)
    assert tool.content_digest and len(tool.content_digest) == 64
    assert not any("notes.md" in d.text for d in diagnostics), "a skill's references are payload"


def test_a_yaml_or_md_without_a_type_outside_a_skill_is_payload_with_a_warning(root: Path):
    (root / "manifest.yaml").write_text("schema_version: 1\n")
    diagnostics: list[Diagnostic] = []
    entities = _read(root, diagnostics)
    assert "README" not in entities and "manifest" not in entities
    warned = {str(d.where.relative_to(root)) for d in diagnostics if d.level is Level.WARN}
    assert warned == {"README.md", "manifest.yaml"}


@pytest.mark.parametrize(
    "rel, text, fragment",
    [
        (
            "alpha.yaml",
            "schema_version: 1\ntype: composite\nexpr: x\ncolour: red\n",
            "unknown key 'colour'",
        ),
        ("alpha.yaml", "schema_version: 1\ntype: composite\n", "missing 'expr'"),
        (
            "alpha.yaml",
            "schema_version: 1\ntype: composite\nexpr: x\ntags: role\n",
            "'tags' must be a list",
        ),
        ("alpha.yaml", "type: composite\nexpr: x\n", "missing 'schema_version'"),
        ("alpha.yaml", "schema_version: 2\ntype: composite\nexpr: x\n", "schema_version 2"),
        ("alpha.yaml", "schema_version: 1\ntype: widget\n", "unknown type 'widget'"),
        ("alpha.yaml", "- a\n- b\n", "not a mapping"),
        ("alpha/prompt.md", "---\nschema_version: 1\ntype: prompt\n---\nX\n", "missing 'block'"),
        (
            "alpha/prompt.md",
            "---\nschema_version: 1\ntype: prompt\nblock: [a]\n---\nX\n",
            "'block' must be a string",
        ),
        (
            "skills/tool/SKILL.md",
            "---\nname: t\nai_hats:\n  schema_version: 1\n  type: skill\n  hooks: []\n---\n",
            "unknown key 'hooks'",
        ),
        (
            "alpha.yaml",
            "schema_version: 1\ntype: composite\nexpr: 'a +'\n",
            "ends with an operator",
        ),
        ("alpha/prompt.md", "---\nblock: [\n---\nX\n", "invalid YAML"),
    ],
)
def test_a_schema_violation_refuses_naming_the_file(root: Path, rel: str, text: str, fragment: str):
    (root / rel).write_text(text)
    with pytest.raises(ProcessingError, match=fragment) as caught:
        _read(root)
    assert rel in str(caught.value)


def test_an_empty_apply_is_a_parse_refusal(root: Path):
    (root / "arm.yaml").write_text(
        'schema_version: 1\ntype: override\napply: ""\nexpr: "+ extra"\n'
    )
    with pytest.raises(ProcessingError, match="apply") as caught:
        _read(root)
    assert "arm.yaml" in str(caught.value)


def test_a_narrow_apply_still_parses(root: Path):
    (root / "arm.yaml").write_text(
        'schema_version: 1\ntype: override\napply: "^nope$"\nexpr: "+ extra"\n'
    )
    entities = _read(root)
    assert entities["arm"].apply.pattern == "^nope$"


def test_a_skill_md_without_the_type_is_payload_with_a_warning(root: Path):
    (root / "skills" / "tool" / "SKILL.md").write_text(
        "---\nname: tool\ndescription: d\n---\n# Tool\n"
    )
    diagnostics: list[Diagnostic] = []
    entities = _read(root, diagnostics)
    assert "skills::tool" not in entities
    assert any("SKILL.md" in str(d.where) and "ai_hats.type" in d.text for d in diagnostics)


def test_a_symlinked_directory_is_read_as_its_contents(root: Path, tmp_path: Path):
    outside = tmp_path / "outside" / "extra"
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text(
        "---\nname: extra\nai_hats:\n  schema_version: 1\n  type: skill\n---\n# Extra\n"
    )
    (root / "skills" / "extra").symlink_to(outside, target_is_directory=True)
    entities = _read(root)
    assert "skills::extra" in entities


def test_nested_roots_refuse_with_both_paths(root: Path):
    inner = root / "alpha"
    with pytest.raises(ProcessingError, match="nested") as caught:
        read_roots([Root(root), Root(inner)], [])
    assert str(root) in str(caught.value) and str(inner) in str(caught.value)


def test_a_root_named_twice_is_read_once(root: Path):
    library = read_roots([Root(root), Root(root)], [])
    assert len(library.layers) == 1


def test_candidate_roots_map_the_chain_onto_experimental(tmp_path: Path):
    builtin = tmp_path / "lib"
    for layer in ("core", "usage", "ai-hats-dev"):
        (builtin / layer).mkdir(parents=True)
    (builtin / "experimental" / "core").mkdir(parents=True)
    (builtin / "experimental" / "ai-hats-dev").mkdir(parents=True)
    user = tmp_path / "user"
    (user / "experimental").mkdir(parents=True)
    project = tmp_path / "proj" / "libraries"
    project.mkdir(parents=True)  # no experimental/ yet: not a candidate root

    chain = [builtin / "core", builtin / "usage", builtin / "ai-hats-dev", user, project]
    roots = candidate_roots(chain, builtin_root=builtin)
    assert [r.path for r in roots] == [
        builtin / "experimental" / "core",
        builtin / "experimental" / "ai-hats-dev",
        user / "experimental",
    ]


def test_the_sample_is_the_documented_shape():
    """The fixture other test files lean on stays what its docstring says."""
    composites = [k for k, v in SAMPLE.items() if "type: composite" in v]
    assert len(composites) == 3
    blocks = {
        line.split(": ")[1]
        for v in SAMPLE.values()
        for line in v.splitlines()
        if line.startswith("block: ")
    }
    assert blocks == {"body", "PRIORITIES", "RULES"}
