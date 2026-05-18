"""Unit tests for pipeline.harness_policy (HATS-378 Phase 0)."""

from __future__ import annotations

import pytest

from ai_hats.pipeline.harness_policy import (
    HarnessPolicy,
    HarnessPolicyError,
    TimeoutPolicy,
    parse_harness_policy,
)


def test_empty_mapping_is_default_policy():
    policy = parse_harness_policy({})
    assert policy == HarnessPolicy()
    assert policy.reporting is False
    assert policy.on_zero_output is None
    assert policy.on_timeout is None


def test_reporting_true_only():
    policy = parse_harness_policy({"reporting": True})
    assert policy.reporting is True
    assert policy.on_zero_output is None
    assert policy.on_timeout is None


def test_on_zero_output_harness_incident():
    policy = parse_harness_policy(
        {"reporting": True, "on_zero_output": "harness_incident"}
    )
    assert policy.on_zero_output == "harness_incident"


def test_on_zero_output_ignore():
    policy = parse_harness_policy(
        {"reporting": True, "on_zero_output": "ignore"}
    )
    assert policy.on_zero_output == "ignore"


def test_on_timeout_full_block():
    policy = parse_harness_policy(
        {
            "on_timeout": {
                "retry": 1,
                "budget_multiplier": 2.5,
                "then": "harness_incident",
            }
        }
    )
    assert policy.on_timeout == TimeoutPolicy(
        retry=1, budget_multiplier=2.5, then="harness_incident"
    )


def test_on_timeout_defaults_when_only_one_key_given():
    policy = parse_harness_policy({"on_timeout": {"retry": 0}})
    assert policy.on_timeout == TimeoutPolicy(
        retry=0, budget_multiplier=2.0, then="harness_incident"
    )


def test_on_timeout_empty_mapping_uses_defaults():
    policy = parse_harness_policy({"on_timeout": {}})
    assert policy.on_timeout == TimeoutPolicy()


def test_not_a_mapping_raises():
    with pytest.raises(HarnessPolicyError, match="must be a mapping"):
        parse_harness_policy("reporting: true")


def test_unknown_top_level_key_raises():
    with pytest.raises(HarnessPolicyError, match="unknown keys"):
        parse_harness_policy({"reporting": True, "bogus": 1})


def test_reporting_wrong_type_raises():
    with pytest.raises(HarnessPolicyError, match="reporting must be bool"):
        parse_harness_policy({"reporting": "yes"})


def test_on_zero_output_invalid_value_raises():
    with pytest.raises(HarnessPolicyError, match="on_zero_output"):
        parse_harness_policy({"on_zero_output": "retry"})


def test_on_timeout_not_a_mapping_raises():
    with pytest.raises(HarnessPolicyError, match="on_timeout must be a mapping"):
        parse_harness_policy({"on_timeout": "retry-once"})


def test_on_timeout_unknown_key_raises():
    with pytest.raises(HarnessPolicyError, match="on_timeout: unknown keys"):
        parse_harness_policy({"on_timeout": {"retries": 1}})


def test_on_timeout_retry_negative_raises():
    with pytest.raises(HarnessPolicyError, match="retry must be a non-negative"):
        parse_harness_policy({"on_timeout": {"retry": -1}})


def test_on_timeout_retry_bool_rejected():
    # ``True`` is technically an int, but accepting it would be a silent
    # type confusion. Reject explicitly.
    with pytest.raises(HarnessPolicyError, match="retry must be a non-negative"):
        parse_harness_policy({"on_timeout": {"retry": True}})


def test_on_timeout_multiplier_below_one_raises():
    with pytest.raises(HarnessPolicyError, match="budget_multiplier must be >= 1.0"):
        parse_harness_policy({"on_timeout": {"budget_multiplier": 0.5}})


def test_on_timeout_multiplier_non_numeric_raises():
    with pytest.raises(HarnessPolicyError, match="budget_multiplier must be a number"):
        parse_harness_policy({"on_timeout": {"budget_multiplier": "x2"}})


def test_on_timeout_then_invalid_raises():
    with pytest.raises(HarnessPolicyError, match="then must be 'harness_incident'"):
        parse_harness_policy({"on_timeout": {"then": "ignore"}})


def test_policy_is_frozen():
    policy = parse_harness_policy({"reporting": True})
    with pytest.raises(Exception):
        policy.reporting = False  # type: ignore[misc]


def test_timeout_policy_is_frozen():
    policy = parse_harness_policy({"on_timeout": {}})
    assert policy.on_timeout is not None
    with pytest.raises(Exception):
        policy.on_timeout.retry = 5  # type: ignore[misc]
