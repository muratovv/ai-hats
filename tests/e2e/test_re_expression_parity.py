"""e2e (HATS-2134)

comment-length: allow

flow:   a person starts a candidate expression with the same spelling as a role
cmds:
    ai-hats -re --dry-run-json maintainer
    ai-hats -re --dry-run-json -- maintainer
    ai-hats -re --dry-run-json maintainer+worker
    ai-hats -re --dry-run-json "maintainer + worker"
    ai-hats maintainer  # no-resolve: bare word, the refusal this flow pins
    ai-hats -r maintainer --dry-run
expect: the bare -re form matches the escaped form, a plus without spaces
        reaches the expression parser, a quoted sum stays an expression, a bare
        word without -re is still refused, and -r still dry-runs the role
why:    -re is a flag, so the expression stayed in the leftover and the argv
        guard treated one word as a missing command
"""

# comment-length: allow — catalog flow block for HATS-2134

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.integration]

_GUARD = "is not an ai-hats command"


def _record(tmp_project, *args: str) -> dict:
    result = tmp_project.run(*args, timeout=30.0).expect_ok()
    return json.loads(result.stdout)


def test_a_single_token_expression_matches_the_escaped_launch(tmp_project) -> None:
    direct = _record(tmp_project, "-re", "--dry-run-json", "maintainer")
    escaped = _record(tmp_project, "-re", "--dry-run-json", "--", "maintainer")
    assert direct["role"] == escaped["role"] == "maintainer"
    assert direct["composition"]["identity"] == escaped["composition"]["identity"]
    assert direct["composition"]["dsl"] == escaped["composition"]["dsl"] == "re"


def test_a_plus_without_spaces_reaches_the_parser(tmp_project) -> None:
    """The guard's refusal must stay findable on the bare word."""
    bare = tmp_project.run("maintainer", timeout=15.0).expect_exit(2)
    assert _GUARD in bare.output
    for args in (
        ("-re", "--dry-run-json", "maintainer+worker"),
        ("-re", "--dry-run-json", "--", "maintainer+worker"),
    ):
        result = tmp_project.run(*args, timeout=30.0).expect_exit(2)
        assert _GUARD not in result.output
        assert "separate token" in result.output


def test_a_quoted_sum_stays_an_expression(tmp_project) -> None:
    record = _record(tmp_project, "-re", "--dry-run-json", "maintainer + worker")
    assert record["role"] == "maintainer + worker"
    assert record["composition"]["dsl"] == "re"


def test_the_reference_role_flag_still_dry_runs(tmp_project) -> None:
    shown = tmp_project.run("-r", "maintainer", "--dry-run", timeout=30.0).expect_ok()
    assert "maintainer" in shown.stdout
    assert _GUARD not in shown.output
