"""Layer order (ADR-0035 D3): overrides apply after every assignment, so a
lower layer's ``apply: ".*"`` still reaches a composite that exists only above."""

from __future__ import annotations

from pathlib import Path

from ai_hats.diagnostics import Diagnostic
from ai_hats.processing.expr import parse_expr
from ai_hats.processing.fold import expand_and_fold
from ai_hats.processing.names import merge
from ai_hats.processing.order import program
from ai_hats.processing.roots import Root, read_roots
from ai_hats.surfaces.plan import TraceEntry
from tests._flat_library import write_root


def _prompt(text: str) -> str:
    return f"---\nschema_version: 1\ntype: prompt\nblock: body\n---\n{text}\n"


def _fold(roots: list[Path], expr: str, diagnostics: list[Diagnostic] | None = None):
    sink: list[Diagnostic] = diagnostics if diagnostics is not None else []
    library = read_roots([Root(r) for r in roots], sink)
    return expand_and_fold(program(library, merge(library, sink), parse_expr(expr)), expr, sink)


def test_a_lower_apply_all_reaches_a_composite_defined_only_above(tmp_path: Path):
    """A core ``apply: ".*"`` appends after the project's assignment of the role."""
    core = write_root(
        tmp_path / "core",
        {
            "arm.yaml": 'schema_version: 1\ntype: override\napply: ".*"\nexpr: "+ extra"\n',
            "extra.md": _prompt("EXTRA"),
        },
    )
    project = write_root(
        tmp_path / "project",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base\ntags: [role]\n",
            "base.md": _prompt("BASE"),
        },
    )
    folded = _fold([core, project], "role")
    assert folded.members == ("base", "extra")
    assert TraceEntry("extra", "arm", None) in folded.trace


def test_an_override_that_selects_nobody_warns(tmp_path: Path):
    """A stanza aimed at a role the library does not have is reported, and the plan still folds."""
    root = write_root(
        tmp_path / "lib",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base\n",
            "base.md": _prompt("BASE"),
            "gone.yaml": 'schema_version: 1\ntype: override\napply: "^go-dev-full$"\nexpr: "+ extra"\n',
            "extra.md": _prompt("EXTRA"),
        },
    )
    diagnostics: list[Diagnostic] = []
    folded = _fold([root], "role", diagnostics)
    assert folded.members == ("base",)
    assert [d.text for d in diagnostics] == ["gone selects no composite"]


def test_two_overrides_apply_in_layer_then_path_order(tmp_path: Path):
    """Layer index wins over the path, so a later root cannot jump ahead by its filename."""
    lower = write_root(
        tmp_path / "m-lower",
        {
            "z.yaml": 'schema_version: 1\ntype: override\napply: ".*"\nexpr: "+ from-lower"\n',
            "from-lower.md": _prompt("LOWER"),
        },
    )
    upper = write_root(
        tmp_path / "a-upper",
        {
            "a.yaml": 'schema_version: 1\ntype: override\napply: ".*"\nexpr: "+ from-upper"\n',
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base\n",
            "base.md": _prompt("BASE"),
            "from-upper.md": _prompt("UPPER"),
        },
    )
    folded = _fold([lower, upper], "role")
    assert folded.members == ("base", "from-lower", "from-upper")


def test_redefining_a_composite_replaces_its_tags(tmp_path: Path):
    """Tags come from the upper file whole; a lower ``hitl`` does not keep the override matching."""
    lower = write_root(
        tmp_path / "lower",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base\ntags: [hitl]\n",
            "base.md": _prompt("BASE"),
            "for-hitl.yaml": 'schema_version: 1\ntype: override\napply: "^hitl$"\nexpr: "+ extra"\n',
            "extra.md": _prompt("EXTRA"),
        },
    )
    upper = write_root(
        tmp_path / "upper",
        {"role.yaml": "schema_version: 1\ntype: composite\nexpr: base\ntags: [role]\n"},
    )
    diagnostics: list[Diagnostic] = []
    folded = _fold([lower, upper], "role", diagnostics)
    assert folded.members == ("base",)
    assert [d.text for d in diagnostics] == ["for-hitl selects no composite"]
