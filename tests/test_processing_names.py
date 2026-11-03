"""Name resolution (ADR-0035 D2): a term is a suffix over the merged tree —
exactly one match resolves, an exact name wins over a longer suffix, zero and
several suffixes refuse, a type that is never a term refuses after resolving,
last-wins by full name between layers."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.diagnostics import Diagnostic
from ai_hats.processing.errors import ProcessingError
from ai_hats.processing.names import merge, resolve, resolve_term
from ai_hats.processing.parse import Composite, PromptEntity
from ai_hats.processing.roots import Root, read_roots
from tests._flat_library import SAMPLE, write_root


@pytest.fixture
def tree(tmp_path: Path):
    return merge(read_roots([Root(write_root(tmp_path / "lib"))], []), [])


@pytest.mark.parametrize(
    "term, full",
    [
        ("tool", "skills::tool"),
        ("skills::tool", "skills::tool"),
        ("rule-one", "rules::rule-one"),
        ("alpha", "alpha"),
        ("alpha::prompt", "alpha::prompt"),
        ("rack.transition", "ops::rack.transition"),
    ],
)
def test_exactly_one_suffix_match_resolves(tree, term: str, full: str):
    assert resolve(tree, term) == full


def test_an_unknown_name_refuses(tree):
    with pytest.raises(ProcessingError, match="unknown name 'nope'"):
        resolve(tree, "nope")


def test_a_suffix_must_sit_on_a_segment_boundary(tree):
    """``ool`` is a suffix of ``skills::tool`` as a string, not as a name."""
    with pytest.raises(ProcessingError, match="unknown name 'ool'"):
        resolve(tree, "ool")


def test_several_matches_refuse_naming_every_candidate(tree):
    with pytest.raises(ProcessingError, match="'prompt' is ambiguous") as caught:
        resolve(tree, "prompt")
    message = str(caught.value)
    assert all(name in message for name in ("alpha::prompt", "bare::prompt", "shared::prompt"))


def test_an_exact_name_beats_a_longer_suffix(tmp_path: Path):
    """``worker`` is the composite. ``rules::worker`` is written in full."""
    root = write_root(
        tmp_path / "lib",
        {
            "worker.yaml": "schema_version: 1\ntype: composite\nexpr: base\n",
            "base.md": "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nBASE\n",
            "rules/worker.md": (
                "---\nschema_version: 1\ntype: prompt\nblock: RULES\nheading: worker\n---\nRULE\n"
            ),
        },
    )
    tree = merge(read_roots([Root(root)], []), [])
    assert resolve(tree, "worker") == "worker"
    assert resolve(tree, "rules::worker") == "rules::worker"


def test_a_type_that_is_never_a_term_refuses_after_resolving(tree):
    with pytest.raises(
        ProcessingError, match="ops::rack.transition, a\\(n\\) operation.*not a term"
    ):
        resolve_term(tree, "rack.transition")
    assert isinstance(resolve_term(tree, "alpha"), Composite)
    assert isinstance(resolve_term(tree, "rule-one"), PromptEntity)


def test_a_higher_layer_wins_by_full_name_and_a_kind_change_warns(tmp_path: Path):
    lower = write_root(tmp_path / "lower")
    upper = write_root(
        tmp_path / "upper",
        {
            "alpha.yaml": "schema_version: 1\ntype: composite\nexpr: bare\ntags: [TEST]\n",
            "rules/x.sh": "#!/bin/sh\nexit 0\n",
            "rules/rule-one.yaml": (
                "schema_version: 1\ntype: hook\nkind: git\nat: pre-push\nrun: x.sh\n"
            ),
        },
    )
    diagnostics: list[Diagnostic] = []
    tree = merge(read_roots([Root(lower), Root(upper)], []), diagnostics)

    alpha = tree.entities["alpha"]
    assert isinstance(alpha, Composite) and alpha.path == upper / "alpha.yaml"
    assert alpha.tags == ("TEST",), "an override is whole: tags come from the upper file"
    assert tree.entities["rules::rule-one"].path == upper / "rules" / "rule-one.yaml"
    assert [d.text for d in diagnostics] == [
        f"rules::rule-one: a hook overrides a prompt of the same name from {lower / 'rules' / 'rule-one.md'}"
    ]
    assert "shared" in tree.entities, "what the upper layer does not name stays"
    assert SAMPLE["shared.yaml"], "the lower layer is the shared sample"
