"""Unit tests for tag parsing / validation (HATS-163).

Validation is strict — every malformed input path gets its own test here. The
contract is a promise to orchestrators that tag data lands in metrics.json
exactly as written (no silent truncation, no coercion).
"""

from __future__ import annotations

import pytest

from ai_hats.tags import (
    MAX_TAG_KEY_LEN,
    MAX_TAG_VALUE_LEN,
    MAX_TAGS_PER_SESSION,
    RESERVED_TAG_KEYS,
    TagValidationError,
    parse_tag_filters,
    parse_tags,
)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_dict():
    assert parse_tags([]) == {}
    assert parse_tags(()) == {}


def test_single_tag_parsed():
    assert parse_tags(["alert_fp=abc123"]) == {"alert_fp": "abc123"}


def test_multiple_tags_parsed_in_order():
    result = parse_tags([
        "alert_fp=abc123",
        "alertname=ImmichContainerDown",
        "client=home-lab",
    ])
    assert result == {
        "alert_fp": "abc123",
        "alertname": "ImmichContainerDown",
        "client": "home-lab",
    }


def test_value_may_contain_equals_sign():
    """Split on FIRST `=` only — values can legitimately contain `=`
    (base64, query strings, JSON)."""
    result = parse_tags(["payload=a=b&c=d"])
    assert result == {"payload": "a=b&c=d"}


def test_value_may_contain_spaces_and_punctuation():
    result = parse_tags(["title=Immich container went down!"])
    assert result == {"title": "Immich container went down!"}


@pytest.mark.parametrize("key", [
    "a", "A", "_", "_foo", "x", "alert_fp", "alert.name", "client-id",
    "Path.To.Value", "a1", "K123_foo.bar-baz",
])
def test_valid_key_characters(key):
    assert parse_tags([f"{key}=x"]) == {key: "x"}


# ---------------------------------------------------------------------------
# Format violations
# ---------------------------------------------------------------------------


def test_missing_equals_raises():
    with pytest.raises(TagValidationError, match="missing '=' separator"):
        parse_tags(["no_equals_here"])


def test_empty_key_raises():
    with pytest.raises(TagValidationError, match="key must not be empty"):
        parse_tags(["=value"])


def test_empty_value_raises():
    with pytest.raises(TagValidationError, match="value for key 'key' must not be empty"):
        parse_tags(["key="])


@pytest.mark.parametrize("bad_key", [
    "1starts_with_digit",
    "-starts-with-dash",
    ".starts.with.dot",
    "has space",
    "has/slash",
    "has:colon",
    "has+plus",
    "кириллица",
])
def test_invalid_key_characters_raise(bad_key):
    with pytest.raises(TagValidationError, match="must match"):
        parse_tags([f"{bad_key}=v"])


# ---------------------------------------------------------------------------
# Length limits
# ---------------------------------------------------------------------------


def test_key_at_max_length_ok():
    key = "a" * MAX_TAG_KEY_LEN
    assert parse_tags([f"{key}=v"]) == {key: "v"}


def test_key_over_max_length_raises():
    key = "a" * (MAX_TAG_KEY_LEN + 1)
    with pytest.raises(TagValidationError, match=f"exceeds {MAX_TAG_KEY_LEN} chars"):
        parse_tags([f"{key}=v"])


def test_value_at_max_length_ok():
    value = "x" * MAX_TAG_VALUE_LEN
    assert parse_tags([f"k={value}"]) == {"k": value}


def test_value_over_max_length_raises():
    value = "x" * (MAX_TAG_VALUE_LEN + 1)
    with pytest.raises(TagValidationError, match=f"exceeds {MAX_TAG_VALUE_LEN} chars"):
        parse_tags([f"k={value}"])


# ---------------------------------------------------------------------------
# Reserved keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reserved_key", sorted(RESERVED_TAG_KEYS))
def test_each_reserved_key_rejected(reserved_key):
    with pytest.raises(TagValidationError, match="is reserved"):
        parse_tags([f"{reserved_key}=anything"])


def test_reserved_key_rejected_also_in_filters():
    """Filter-side validation shares the same reserved-key guard: users should
    filter by ``role`` via the dedicated ``--role`` flag, not ``--tag role=X``."""
    with pytest.raises(TagValidationError, match="is reserved"):
        parse_tag_filters(["role=primary"])


# ---------------------------------------------------------------------------
# Count limit + duplicates
# ---------------------------------------------------------------------------


def test_count_at_max_ok():
    raw = [f"k{i}=v{i}" for i in range(MAX_TAGS_PER_SESSION)]
    assert len(parse_tags(raw)) == MAX_TAGS_PER_SESSION


def test_count_over_max_raises():
    raw = [f"k{i}=v{i}" for i in range(MAX_TAGS_PER_SESSION + 1)]
    with pytest.raises(TagValidationError, match="too many tags"):
        parse_tags(raw)


def test_duplicate_key_raises():
    with pytest.raises(TagValidationError, match="duplicate tag key 'k'"):
        parse_tags(["k=a", "k=b"])


# ---------------------------------------------------------------------------
# parse_tag_filters — same surface as parse_tags
# ---------------------------------------------------------------------------


def test_filters_parse_same_as_tags():
    assert parse_tag_filters(["alert_fp=abc", "client=home"]) == {
        "alert_fp": "abc",
        "client": "home",
    }
