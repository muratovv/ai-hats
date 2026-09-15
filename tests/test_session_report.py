"""The dry-run report: a rendering of the record, never a second derivation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ai_hats_core import ConsentPoint

from ai_hats.materialization import PlanMaterializer
from ai_hats.session_artifacts import SessionPolicy
from ai_hats.session_report import SessionReport


def _report(tmp_path: Path) -> SessionReport:
    port = PlanMaterializer()
    port.mkdir(tmp_path / "cache")
    port.write_text(tmp_path / "cache" / "prompt.md", "role text")
    return SessionReport(
        role="maintainer",
        provider="agy",
        run_mode="hitl",
        policy=SessionPolicy(),
        launch=["agy", "--add-dir", str(tmp_path / "cache" / "rules")],
        env={"AI_HATS_DIR": "/secret/path", "AI_HATS_PYTHON": "/venv/bin/python"},
        prompt=tmp_path / "cache" / "prompt.md",
        record=port.record,
    )


def test_json_payload_carries_the_launch_and_the_record(tmp_path: Path):
    payload = _report(tmp_path).to_dict()

    assert payload["role"] == "maintainer"
    assert payload["run_mode"] == "hitl"
    assert payload["launch"][0] == "agy"
    assert payload["policy"] == {"context": True, "hooks": True, "settings": True}
    kinds = [e["kind"] for e in payload["materialized"]]
    assert kinds == ["mkdir", "write_text"]
    assert payload["materialized"][1]["size"] == 9


def test_env_values_never_appear_in_either_rendering(tmp_path: Path):
    """R5: env carries secrets — only key names may be reported."""
    report = _report(tmp_path)

    as_json = json.dumps(report.to_dict())
    as_text = report.render()

    assert "AI_HATS_DIR" in as_json and "AI_HATS_DIR" in as_text
    assert "/secret/path" not in as_json
    assert "/secret/path" not in as_text
    assert "/venv/bin/python" not in as_json
    assert "/venv/bin/python" not in as_text


def test_text_rendering_shows_launch_and_materialized_paths(tmp_path: Path):
    text = _report(tmp_path).render()

    assert "agy --add-dir" in text
    assert "prompt.md" in text
    assert "maintainer" in text


def test_duplicate_materialization_is_surfaced(tmp_path: Path):
    """R7 signal (b) must reach the human, not just the assertion."""
    port = PlanMaterializer()
    port.write_text(tmp_path / "p", "x")
    port.write_text(tmp_path / "p", "x")
    report = SessionReport(
        role="r",
        provider="claude",
        run_mode="automate",
        policy=SessionPolicy(),
        launch=["claude"],
        env={},
        prompt=None,
        record=port.record,
    )

    assert report.to_dict()["duplicates"] == [str(tmp_path / "p")]
    assert "materialized twice" in report.render()


def test_materialized_entries_carry_sha256_digests(tmp_path: Path):
    import hashlib

    port = PlanMaterializer()
    port.write_text(tmp_path / "test.txt", "hello world")
    report = SessionReport(
        role="r",
        provider="claude",
        run_mode="hitl",
        policy=SessionPolicy(),
        launch=["claude"],
        env={},
        prompt=None,
        record=port.record,
    )
    mat = report.to_dict()["materialized"]
    assert len(mat) == 1
    expected_digest = hashlib.sha256(b"hello world").hexdigest()
    assert mat[0]["digest"] == expected_digest


def test_full_render_dumps_the_body_a_plan_mode_build_never_wrote(tmp_path: Path):
    """HATS-1548: ``--dry-run-full`` promised the composed prompt and printed
    ``(not written)`` — it read the path off disk, and a dry-run writes nothing.

    The bytes were in ``BuiltArtifacts.full_content`` the whole time. Body stays
    out of ``to_dict``: it never was in the payload, and putting ~50 KB of prompt
    into ``--json`` (and into the goldens) to fix a human affordance is a trade
    nobody asked for.
    """  # comment-length: allow — the render/json asymmetry is deliberate
    port = PlanMaterializer()
    port.write_text(tmp_path / "cache" / "prompt.md", "role text")
    report = SessionReport(
        role="maintainer",
        provider="agy",
        run_mode="hitl",
        policy=SessionPolicy(),
        launch=["agy"],
        env={},
        prompt=tmp_path / "cache" / "prompt.md",
        record=port.record,
        prompt_text="# ROLE: MAINTAINER\nbody bytes",
    )

    assert not (tmp_path / "cache" / "prompt.md").exists(), "a dry-run writes nothing"
    assert "# ROLE: MAINTAINER\nbody bytes" in report.render(full=True)
    assert "(not written)" not in report.render(full=True)
    assert "prompt_text" not in report.to_dict()


def test_full_render_still_falls_back_to_the_file_on_a_real_record(tmp_path: Path):
    """A launch record read back from disk carries no body — the file does."""
    written = tmp_path / "prompt.md"
    written.write_text("bytes on disk")
    report = SessionReport(
        role="maintainer",
        provider="agy",
        run_mode="hitl",
        policy=SessionPolicy(),
        launch=["agy"],
        env={},
        prompt=written,
        record=PlanMaterializer().record,
    )

    assert "bytes on disk" in report.render(full=True)


def _consent(app: str, path: tuple[str, ...], selector: str) -> ConsentPoint:
    return ConsentPoint(declared_by="trait-agent", app=app, path=path, selector=selector)


def test_consent_section_names_the_field_each_reader_keys_on(tmp_path: Path):
    """HATS-1726: three grammars ride one list, and each is found by a DIFFERENT
    field — printing one selector for all of them would be a plausible lie about
    the one grammar the section exists for.

    Measured: `_grant_policy` keys on `selector`, `declared_consent_targets` on
    `to`, and the wt question on the exact (app, selector) pair.
    """
    report = replace(
        _report(tmp_path),
        consent=(
            _consent("consent_gate", (), "rack.transition"),
            _consent("consent_gate", (), "wt.merge"),
            _consent("rack", ("tasks",), "plan->execute"),
            _consent("rack", ("tasks",), "->done"),
            _consent("wt", (), "pre-merge"),
        ),
    )

    text = report.render()

    assert "\nconsent\n" in text
    # The selector is quoted in every channel that prints it: bare, `->` is a
    # shell redirect, so a copied line stops being a selector (HATS-1733).
    assert "'rack.transition'" in text
    assert "'->done'" in text
    # consent_gate: read as an operation type, by `selector`.
    assert "operation type" in text
    # rack: read by `to`, NEVER by the selector — so the target is what shows.
    assert "entering 'execute'" in text
    assert "entering 'done'" in text
    # wt: found by the exact pair, so the point itself is the key.
    assert "wt point" in text
    assert text.count("by trait-agent") == 5


def test_the_shipped_declaration_renders_each_grammar_by_its_own_field():
    """HATS-1790: the same column, asked of the LIBRARY instead of a fixture.

    The sibling above proves it on hand-written rows in a spelling composition
    stopped producing at HATS-1755 — `apps.rack` / `apps.wt` consent rows do not
    occur any more. So it stayed green while every shipped row rendered the same
    label, which is the one thing HATS-1726 built this column to prevent: three
    grammars ride one list and each is found by a DIFFERENT field.
    """
    from ai_hats.assembler import Assembler
    from ai_hats.session_report import _consent_key, consent_entry

    repo = Path(__file__).resolve().parent.parent
    library = repo / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
    result = Assembler(repo, library_paths=[library / "core", library / "usage"]).composer.compose(
        "maintainer"
    )
    assert result.errors == [], result.errors

    rendered = {}
    for point in result.consent:
        entry = consent_entry(point)
        rendered[entry["selector"]] = _consent_key(entry)

    assert rendered == {
        "plan->execute": "entering 'execute'",
        "->done": "entering 'done'",
        "pre-merge": "wt point",
        "pre-discard": "wt point",
    }


def test_a_role_declaring_no_consent_says_so_instead_of_dropping_the_section(tmp_path: Path):
    """A role asking about nothing, and a section that did not render, differ.

    The sibling `checks` section already draws this line with `(none bound)`;
    a consent section that vanishes when empty rebuilds the exact silence this
    report exists to remove (HATS-1726).
    """
    text = _report(tmp_path).render()

    assert "\nconsent\n" in text
    assert "(none declared)" in text


def _composition():
    from ai_hats.surfaces import HookEvent
    from ai_hats.surfaces.plan import (
        CompositionPlan,
        Executable,
        ExternalHook,
        Hooks,
        OnError,
        Prompt,
        PromptBlock,
        PromptMember,
        RuntimeHook,
        Skill,
        TraceEntry,
    )

    def payload(name: str) -> Executable:
        return Executable(path=Path("/lib/skills") / name, content_digest="ab" * 32)

    return CompositionPlan(
        identity="maintainer + sre",
        prompt=Prompt(
            blocks=(
                PromptBlock("PRIORITIES", (PromptMember("maintainer::priorities", "1. x", None),)),
                PromptBlock(None, (PromptMember("maintainer::prompt", "# t", None),)),
                PromptBlock(
                    "RULES",
                    (
                        PromptMember(
                            "rules::rule_backlog_discipline", "R", "rule_backlog_discipline"
                        ),
                    ),
                ),
            ),
        ),
        skills=(
            Skill("skills::safety-guard", Path("/lib/skills/safety-guard"), "cd" * 32),
            Skill("skills::hatrack", Path("/lib/skills/hatrack"), "ef" * 32),
        ),
        hooks=Hooks(
            runtime=(
                RuntimeHook(
                    HookEvent.PRE_TOOL_USE, "Bash", payload("safety-guard/hooks/safety_gate.py")
                ),
            ),
            external=(
                ExternalHook(
                    "git",
                    None,
                    "pre-push",
                    payload("quality-gate/git_hooks/pre-push-e2e-master.sh"),
                    None,
                    "skills::quality-gate",
                ),
                ExternalHook(
                    "rack",
                    "tasks",
                    "->done",
                    payload("quality-gate/hooks/done-gate.sh"),
                    OnError.REFUSE,
                    "ai-hats-gates",
                ),
                ExternalHook(
                    "wt", None, "teardown[merge]", payload("x/hooks/drain.sh"), None, "skills::x"
                ),
                ExternalHook(
                    "consent_gate", "rack.transition", "plan->execute", None, None, "trait-agent"
                ),
            ),
        ),
        trace=(
            TraceEntry("trait-agent", "maintainer", None),
            TraceEntry("skills::hatrack", "trait-agent", "overrides::project"),
        ),
    )


def test_composition_section_shows_hooks_by_kind_and_consent_ends(tmp_path: Path):
    report = replace(_report(tmp_path), composition=_composition())

    payload = report.to_dict()
    text = report.render()

    composition = payload["composition"]
    assert composition["identity"] == "maintainer + sre"
    assert len(composition["digest"]) == 64
    assert composition["prompt"]["blocks"][2] == {
        "name": "RULES",
        "members": [
            {"name": "rules::rule_backlog_discipline", "heading": "rule_backlog_discipline"}
        ],
    }
    assert "text" not in composition["prompt"], "bytes stay out of the record"
    assert "text" not in json.dumps(composition["prompt"]), "member text stays out too"
    assert composition["skills"][1]["name"] == "skills::hatrack"
    assert composition["skills"][1]["content_digest"] == "ef" * 32
    assert (
        composition["hooks"]["runtime"][0]["run"]["path"]
        == "/lib/skills/safety-guard/hooks/safety_gate.py"
    )
    assert composition["hooks"]["external"][1] == {
        "app": "rack",
        "object": "tasks",
        "at": "->done",
        "run": {
            "path": "/lib/skills/quality-gate/hooks/done-gate.sh",
            "content_digest": "ab" * 32,
            "digest": composition["hooks"]["external"][1]["run"]["digest"],
        },
        "on_error": "refuse",
        "declared_by": "ai-hats-gates",
    }
    assert composition["hooks"]["external"][3] == {
        "app": "consent_gate",
        "object": "rack.transition",
        "at": "plan->execute",
        "run": None,
        "on_error": None,
        "declared_by": "trait-agent",
    }
    assert composition["trace"][1]["removed_by"] == "overrides::project"
    assert "diagnostics" not in composition, "findings ride the payload's sink, not the plan"

    assert "\ncomposition  maintainer + sre" in text
    assert "runtime   PreToolUse  Bash  /lib/skills/safety-guard/hooks/safety_gate.py" in text
    assert (
        "external  git 'pre-push'  /lib/skills/quality-gate/git_hooks/pre-push-e2e-master.sh  by skills::quality-gate"
        in text
    )
    assert (
        "external  rack.tasks '->done'  /lib/skills/quality-gate/hooks/done-gate.sh  on_error=refuse  by ai-hats-gates"
        in text
    )
    assert "external  wt 'teardown[merge]'  /lib/skills/x/hooks/drain.sh  by skills::x" in text
    assert "external  consent_gate.rack.transition 'plan->execute'  by trait-agent" in text
    assert "skills::hatrack" in text and "removed by overrides::project" in text
    assert "1 PRIORITIES, 1 (prose), 1 RULES" in text


def test_an_entry_shows_its_source_and_one_from_outside_the_composition_is_marked(tmp_path: Path):
    """A link into the person's home is a planning input, not a skill mirror;
    the reader must be able to tell the two apart without --json."""
    from ai_hats.surfaces.plan import Skill

    skill = tmp_path / "lib" / "skills" / "hatrack"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# h\n")
    home_entry = tmp_path / "home" / ".config" / "opencode" / "themes"
    port = PlanMaterializer()
    port.copy_tree(skill, tmp_path / "cache" / "skills" / "hatrack")
    port.symlink(home_entry, tmp_path / "cache" / "opencode" / "themes")
    port.write_text(tmp_path / "cache" / "prompt.md", "role text")
    report = replace(
        _report(tmp_path),
        record=port.record,
        composition=replace(
            _composition(), skills=(Skill("skills::hatrack", skill, "ef" * 32),)
        ),
    )

    text = report.render()

    mirror, link, prompt = (line for line in text.splitlines() if line.startswith("  copy_tree") or line.startswith("  symlink") or line.startswith("  write_text"))
    assert f"<- {skill}" in mirror and "outside" not in mirror
    assert f"<- {home_entry}" in link and "outside the composition" in link
    assert "<-" not in prompt


def test_a_report_without_a_composition_carries_no_section(tmp_path: Path):
    report = _report(tmp_path)
    assert "composition" not in report.to_dict()
    assert "\ncomposition" not in report.render()
