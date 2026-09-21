"""e2e (HATS-2009)

flow:   a maintainer projects the plan of an expression over the candidate library
        and reads the prompt of a bare trait, which the reference path refuses
cmds:
    ai-hats -re dry-run "hypothesis-intake + worker" --json
    ai-hats -re show-prompt worker
    ai-hats config show-prompt --role worker
expect: the record's composition names the canonical expression, the prompt members
        in term order with the worker's prose last, the skills with their trees and
        digests, no hooks; the trait's prompt prints at exit 0 while the reference
        exits 2 on the same trait
why:    the two projections are the only readers of the candidate plan until a
        session runs from it; the trait that could not be shown is the measured
        baseline the flat model exists to remove
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.library]


def test_dry_run_json_projects_the_candidate_plan(tmp_project) -> None:
    result = tmp_project.run("-re", "dry-run", "hypothesis-intake + worker", "--json").expect_ok()
    record = json.loads(result.stdout)
    composition = record["composition"]
    assert record["role"] == "hypothesis-intake + worker"
    assert composition["identity"] == "hypothesis-intake + worker"

    body, *named = composition["prompt"]["blocks"]
    members = [m["name"] for m in body["members"]]
    assert body["name"] is None
    assert members[0] == "hypothesis-intake::prompt" and members[-1] == "worker::prompt"
    assert {b["name"] for b in named} == {"PRIORITIES", "RULES"}

    skills = composition["skills"]
    assert [s["name"] for s in skills] == ["skills::safety-guard"]
    assert skills[0]["path"].endswith("/experimental/core/skills/safety-guard")
    assert len(skills[0]["content_digest"]) == 64
    assert composition["hooks"] == {"runtime": [], "external": []}

    brought = {t["term"]: t["brought_by"] for t in composition["trace"]}
    assert brought["trait-base"] == "hypothesis-intake"
    assert brought["worker"] == "hypothesis-intake + worker"
    assert brought["skills::safety-guard"] == "trait-base"
    assert record["notes"] == [], "a clean expression over the shipped copy warns about nothing"


def test_show_prompt_prints_a_bare_trait_the_reference_refuses(tmp_project) -> None:
    shown = tmp_project.run("-re", "show-prompt", "worker").expect_ok()
    assert shown.stdout.startswith("## WORKER (paired session)")
    # Positive control: the reference path still cannot show a trait (rc=2).
    tmp_project.run("config", "show-prompt", "--role", "worker").expect_exit(2)
