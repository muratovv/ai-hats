"""HATS-1042: DEFAULT_PLAN_SECTIONS is file-backed by the packaged
plan-sections.yaml. Pins that the file parses to EXACTLY the prior in-code
tuple (names, order, required flags) so the code-table → file move is lossless
and the symbol/type consumers import stays unchanged.
"""

from __future__ import annotations

from ai_hats_rack.extensions.sections import DEFAULT_PLAN_SECTIONS, Section

# The exact catalog the code table declared before the file-backing move.
_EXPECTED = (
    Section(name="Requirements"),
    Section(name="Approach & counter", required=False),
    Section(name="Scope & Out-of-scope"),
    Section(name="Steps"),
    Section(name="Verification Protocol"),
)


def test_default_sections_match_the_pre_move_tuple():
    assert DEFAULT_PLAN_SECTIONS == _EXPECTED


def test_default_sections_type_is_tuple_of_section():
    assert isinstance(DEFAULT_PLAN_SECTIONS, tuple)
    assert all(isinstance(s, Section) for s in DEFAULT_PLAN_SECTIONS)


def test_approach_and_counter_is_the_only_optional_section():
    optional = [s.name for s in DEFAULT_PLAN_SECTIONS if not s.required]
    assert optional == ["Approach & counter"]
