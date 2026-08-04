import pytest

from ai_hats.role_spec import RoleSpec, RoleSpecError, has_operators, parse_role_spec


def test_parse_role_spec_simple():
    spec = parse_role_spec("maintainer")
    assert spec == RoleSpec(
        role="maintainer",
        adds=(),
        removes=(),
        raw="maintainer",
    )


def test_parse_role_spec_hyphenated_name():
    spec = parse_role_spec("trait-base")
    assert spec == RoleSpec(
        role="trait-base",
        adds=(),
        removes=(),
        raw="trait-base",
    )

    spec2 = parse_role_spec("maintainer-leader")
    assert spec2 == RoleSpec(
        role="maintainer-leader",
        adds=(),
        removes=(),
        raw="maintainer-leader",
    )

    spec3 = parse_role_spec("dev::python")
    assert spec3 == RoleSpec(
        role="dev::python",
        adds=(),
        removes=(),
        raw="dev::python",
    )


def test_parse_role_spec_add_and_remove():
    spec = parse_role_spec("maintainer + leader")
    assert spec == RoleSpec(
        role="maintainer",
        adds=("leader",),
        removes=(),
        raw="maintainer + leader",
    )

    spec_compact = parse_role_spec("maintainer+leader")
    assert spec_compact == RoleSpec(
        role="maintainer",
        adds=("leader",),
        removes=(),
        raw="maintainer+leader",
    )

    spec_complex = parse_role_spec("maintainer - trait-base + trait-base-star")
    assert spec_complex == RoleSpec(
        role="maintainer",
        adds=("trait-base-star",),
        removes=("trait-base",),
        raw="maintainer - trait-base + trait-base-star",
    )


def test_parse_role_spec_errors():
    with pytest.raises(RoleSpecError, match="role spec is empty"):
        parse_role_spec("")

    with pytest.raises(RoleSpecError, match="role spec is empty"):
        parse_role_spec("   ")

    with pytest.raises(RoleSpecError, match="role spec must start with a role name, got '\\+'"):
        parse_role_spec("+ leader")

    with pytest.raises(RoleSpecError, match="role spec must start with a role name, got '-'"):
        parse_role_spec("- leader")

    with pytest.raises(RoleSpecError, match="role spec ends with a dangling '\\+'"):
        parse_role_spec("maintainer +")

    with pytest.raises(RoleSpecError, match="role spec ends with a dangling '-'"):
        parse_role_spec("maintainer -")

    with pytest.raises(RoleSpecError, match="expected a component name after '\\+', got '\\+'"):
        parse_role_spec("maintainer + + leader")

    with pytest.raises(RoleSpecError, match="expected a component name after '\\+', got '-'"):
        parse_role_spec("maintainer + - leader")

    with pytest.raises(RoleSpecError, match="expected '\\+' or '-' before 'leader'"):
        parse_role_spec("maintainer leader")

    with pytest.raises(RoleSpecError, match="duplicate operand 'leader' in role spec"):
        parse_role_spec("maintainer + leader + leader")


def test_has_operators():
    assert not has_operators("maintainer")
    assert not has_operators("trait-base")
    assert not has_operators("maintainer-leader")
    assert not has_operators("dev::python")
    assert not has_operators("")

    assert has_operators("maintainer + leader")
    assert has_operators("maintainer+leader")
    assert has_operators("maintainer - trait-base")
    assert has_operators("+ leader")
