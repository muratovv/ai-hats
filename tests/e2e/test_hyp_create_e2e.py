"""e2e: `ai-hats task hyp create --verification-protocol` round-trips the
field into the persisted HYP YAML (HATS-623).

`library-change-hypothesis-protocol` mandates a `verification_protocol`
field on companion HYPs. The Hypothesis model is `extra="allow"` and
`HypothesisStore.create` dumps with `exclude_none=True`, so the field
round-trips once the CLI exposes a flag. Before HATS-623 the protocol
text had to be folded into `--success-criterion`.

Per dev_rule_e2e_gate — real binary, real YAML on disk.
Fail-under-revert: without the option, Click rejects the unknown flag →
non-zero exit.
"""

from __future__ import annotations

import pytest
import yaml

pytestmark = pytest.mark.integration


def _hyp_yaml(project_path, hyp_id: str):
    return (
        project_path / ".agent" / "ai-hats" / "tracker"
        / "hypotheses" / f"{hyp_id}.yaml"
    )


def test_hyp_create_verification_protocol_roundtrip(tmp_project):
    proj = tmp_project
    r = proj.run(
        "task", "hyp", "create",
        "--title", "lib change",
        "--hypothesis", "x causes y",
        "--source-task", "HATS-001",
        "--verification-protocol", "Run suite X; observe metric Y unchanged",
    )
    r.expect_ok().expect_stdout_contains("HYP-001")

    data = yaml.safe_load(_hyp_yaml(proj.path, "HYP-001").read_text())
    assert data["verification_protocol"] == "Run suite X; observe metric Y unchanged"


def test_hyp_create_without_flag_omits_key(tmp_project):
    proj = tmp_project
    r = proj.run(
        "task", "hyp", "create",
        "--title", "no vp",
        "--hypothesis", "x causes y",
        "--source-task", "HATS-001",
    )
    r.expect_ok().expect_stdout_contains("HYP-001")

    data = yaml.safe_load(_hyp_yaml(proj.path, "HYP-001").read_text())
    assert "verification_protocol" not in data
