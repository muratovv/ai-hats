"""e2e (HATS-1969)

flow:   a person previews a candidate role and lists what it arms
cmds:
    ai-hats -re dry-run maintainer --json
    ai-hats -re dry-run maintainer
    ai-hats -re dry-run trait-agent --json
    ai-hats -re dry-run "trait-agent - rack.transition::done" --json
    ai-hats -re show-prompt "trait-agent - rack.transition::done"
    ai-hats -re list hooks maintainer
    ai-hats -re list operations
    ai-hats -re show-prompt maintainer
expect: the done gate and the consent points are visible, a removal is a note,
        show-prompt stays the prompt, and list prints the same gate and the
        four operations
why:    hooks are not in the prompt, so the preview is the only place they show
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.library]


def test_dry_run_shows_gates_and_consent_and_a_removal(tmp_project) -> None:
    # HATS-1969 — the catalog reads the body; the header pin alone is not a basis.
    shown = json.loads(tmp_project.run("-re", "dry-run", "maintainer", "--json").expect_ok().stdout)
    external = shown["composition"]["hooks"]["external"]
    done = [hook for hook in external if hook["app"] == "rack" and hook["at"] == "->done"]
    assert done and done[0]["on_error"] == "refuse" and done[0]["declared_by"]
    text = tmp_project.run("-re", "dry-run", "maintainer").expect_ok().stdout
    assert "checks" in text and "->done" in text

    consent = json.loads(
        tmp_project.run("-re", "dry-run", "trait-agent", "--json").expect_ok().stdout
    )
    points = consent["consent"]
    assert points and {"selector", "path", "from", "to", "declared_by"} <= set(points[0])
    assert any(
        point["selector"] == "->done" and "rack.transition" in point["path"] for point in points
    )

    removed = json.loads(
        tmp_project.run("-re", "dry-run", "trait-agent - rack.transition::done", "--json")
        .expect_ok()
        .stdout
    )
    assert any("disarms consent" in note for note in removed["notes"])
    armed = [point["selector"] for point in removed["consent"]]
    assert "->done" not in armed and "plan->execute" in armed
    prompt = tmp_project.run(
        "-re", "show-prompt", "trait-agent - rack.transition::done"
    ).expect_ok()
    assert "disarms consent" in prompt.stderr
    assert "type: hook" not in prompt.stdout

    listed = tmp_project.run("-re", "list", "hooks", "maintainer").expect_ok().stdout
    assert "->done" in listed
    operations = tmp_project.run("-re", "list", "operations").expect_ok().stdout.split()
    assert operations == ["fs.remove", "rack.transition", "wt.discard", "wt.merge"]

    bare = tmp_project.run("-re", "show-prompt", "maintainer").expect_ok().stdout
    members = [
        member["name"]
        for block in shown["composition"]["prompt"]["blocks"]
        for member in block["members"]
    ]
    assert members and not any(name.startswith("hooks::") for name in members)
    assert "done-gate" not in bare
