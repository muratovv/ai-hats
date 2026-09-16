"""The session record's human rendering: a rendering of the one dict, never a
second derivation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from ai_hats.materialization import (
    MaterializationEntry,
    WriteKind,
    describe_mkdir,
    describe_symlink,
    describe_write_text,
)
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import session_record
from ai_hats.session_report import consent_row, render_report
from ai_hats.surfaces import HookEvent
from ai_hats.surfaces.plan import (
    CompositionPlan,
    Executable,
    ExternalHook,
    Hooks,
    Launch,
    Launched,
    MaterializationPlan,
    OnError,
    Prompt,
    PromptBlock,
    PromptMember,
    RuntimeHook,
    Skill,
    TraceEntry,
)


def _composition(**overrides) -> CompositionPlan:
    def payload(name: str) -> Executable:
        return Executable(path=Path("/lib/skills") / name, content_digest="ab" * 32)

    fields = dict(
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
    fields.update(overrides)
    return CompositionPlan(**fields)


def _plan(
    root: Path, composition: CompositionPlan | None = None, entries=None
) -> MaterializationPlan:
    """An agy-shaped plan: the context lands in a file the launch names."""
    composition = composition or _composition()
    return MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface="agy",
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        entries=tuple(
            entries
            if entries is not None
            else (describe_mkdir(root), describe_write_text(root / "prompt.md", "role text"))
        ),
        env={},
        launch=Launch(args=("--add-dir", str(root / "rules")), sdk_options=None),
        context=root / "prompt.md",
    )


def _launched(plan: MaterializationPlan) -> Launched:
    return Launched(
        args=("agy", *plan.launch.args),
        sdk_options=None,
        env={"AI_HATS_DIR": "/secret/path", "AI_HATS_PYTHON": "/venv/bin/python"},
        prompt="role text",
    )


def _record(tmp_path: Path, **plan_overrides) -> dict:
    plan = _plan(tmp_path / "cache", **plan_overrides)
    return session_record(plan, _launched(plan), role="maintainer", cwd="")


def test_json_payload_carries_the_launch_and_the_record(tmp_path: Path):
    payload = _record(tmp_path)

    assert payload["role"] == "maintainer"
    assert payload["run_mode"] == "hitl"
    assert payload["launch"][0] == "agy"
    assert payload["policy"] == {"context": True, "hooks": True, "settings": True}
    kinds = [e["kind"] for e in payload["materialized"]]
    assert kinds == ["mkdir", "write_text"]
    assert payload["materialized"][1]["size"] == 9
    assert payload["prompt"] == str(tmp_path / "cache" / "prompt.md")


def test_env_values_never_appear_in_either_rendering(tmp_path: Path):
    """R5: env carries secrets — only key names may be reported."""
    record = _record(tmp_path)

    as_json = json.dumps(record)
    as_text = render_report(record)

    assert "AI_HATS_DIR" in as_json and "AI_HATS_DIR" in as_text
    assert "/secret/path" not in as_json
    assert "/secret/path" not in as_text
    assert "/venv/bin/python" not in as_json
    assert "/venv/bin/python" not in as_text


def test_text_rendering_shows_launch_and_materialized_paths(tmp_path: Path):
    text = render_report(_record(tmp_path))

    assert "agy --add-dir" in text
    assert "prompt.md" in text
    assert "maintainer" in text


def test_materialized_entries_carry_sha256_digests(tmp_path: Path):
    root = tmp_path / "cache"
    record = _record(
        tmp_path,
        entries=(describe_mkdir(root), describe_write_text(root / "prompt.md", "hello world")),
    )

    written = [e for e in record["materialized"] if e["kind"] == "write_text"]
    assert len(written) == 1
    assert written[0]["digest"] == hashlib.sha256(b"hello world").hexdigest()


def test_full_render_dumps_the_body_a_dry_run_never_wrote(tmp_path: Path):
    """HATS-1548: ``--dry-run-full`` promised the composed prompt and printed
    ``(not written)`` — it read the path off disk, and a dry-run writes nothing.

    The bytes ride beside the record (``Preview.prompt``), never inside it:
    putting ~50 KB of prompt into ``--json`` (and into the goldens) to fix a
    human affordance is a trade nobody asked for.
    """
    record = _record(tmp_path)

    assert not (tmp_path / "cache" / "prompt.md").exists(), "a dry-run writes nothing"
    text = render_report(record, full=True, prompt_text="# ROLE: MAINTAINER\nbody bytes")
    assert "# ROLE: MAINTAINER\nbody bytes" in text
    assert "(not written)" not in text
    assert "prompt_text" not in record


def test_full_render_still_falls_back_to_the_file_on_a_real_record(tmp_path: Path):
    """A launch record read back from disk carries no body — the file does."""
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "prompt.md").write_text("bytes on disk")

    assert "bytes on disk" in render_report(_record(tmp_path), full=True)


def _consent(app: str, obj: str | None, selector: str) -> ExternalHook:
    return ExternalHook(app, obj, selector, None, None, "trait-agent")


def test_consent_section_names_the_field_each_reader_keys_on(tmp_path: Path):
    """HATS-1726: three grammars ride one list, and each is found by a DIFFERENT
    field — printing one selector for all of them would be a plausible lie about
    the one grammar the section exists for.

    Measured: `_grant_policy` keys on `selector`, `declared_consent_targets` on
    `to`, and the wt question on the exact (app, selector) pair.
    """
    record = _record(tmp_path)
    record["consent"] = [
        consent_row(h)
        for h in (
            _consent("consent_gate", None, "rack.transition"),
            _consent("consent_gate", None, "wt.merge"),
            _consent("rack", "tasks", "plan->execute"),
            _consent("rack", "tasks", "->done"),
            _consent("wt", None, "pre-merge"),
        )
    ]

    text = render_report(record)

    assert "\nconsent\n" in text
    section = text.split("\nconsent\n", 1)[1].split("\ncomposition", 1)[0]
    # The selector is quoted in every channel that prints it: bare, `->` is a
    # shell redirect, so a copied line stops being a selector (HATS-1733).
    assert "'rack.transition'" in section
    assert "'->done'" in section
    # consent_gate: read as an operation type, by `selector`.
    assert "operation type" in section
    # rack: read by `to`, NEVER by the selector — so the target is what shows.
    assert "entering 'execute'" in section
    assert "entering 'done'" in section
    # wt: found by the exact pair, so the point itself is the key.
    assert "wt point" in section
    assert section.count("by trait-agent") == 5


def test_the_shipped_declaration_renders_each_grammar_by_its_own_field(tmp_path: Path):
    """HATS-1790: the same column, asked of the LIBRARY instead of a fixture.

    The sibling above proves it on hand-written rows in a spelling composition
    stopped producing at HATS-1755 — `apps.rack` / `apps.wt` consent rows do not
    occur any more. So it stayed green while every shipped row rendered the same
    label, which is the one thing HATS-1726 built this column to prevent: three
    grammars ride one list and each is found by a DIFFERENT field.
    """
    from ai_hats.assembler import Assembler
    from ai_hats.consent_wrapper import CONSENT_APP
    from ai_hats.materialize import compose_to_run
    from ai_hats.session_report import _consent_key
    from ai_hats.surfaces import adapt

    project = tmp_path / "proj"
    project.mkdir()
    asm = Assembler(project)
    result = compose_to_run(asm, "maintainer")
    plan = adapt(
        result,
        identity="maintainer",
        layout=asm.layout,
        resolver=asm.resolver,
        overlays=(),
        diagnostics=[],
    )

    rendered = {}
    for hook in plan.hooks.external:
        if hook.app == CONSENT_APP:
            entry = consent_row(hook)
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
    record = _record(tmp_path)
    record["consent"] = []

    text = render_report(record)

    assert "\nconsent\n" in text
    assert "(none declared)" in text


def test_composition_section_shows_hooks_by_kind_and_consent_ends(tmp_path: Path):
    record = _record(tmp_path)

    composition = record["composition"]
    text = render_report(record)

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
    assert [c["to"] for c in record["consent"]] == ["execute"], "the consent row, ends parsed"

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


def test_the_checks_section_names_the_gate_and_where_it_runs_from(tmp_path: Path):
    """The rack row of the composition is a check binding; the record says
    which mirror holds its bytes, off the plan's own ``copy_tree`` entry."""
    root = tmp_path / "cache"
    mirror = root / "skills" / "quality-gate"
    record = _record(
        tmp_path,
        entries=(
            describe_mkdir(root),
            describe_write_text(root / "prompt.md", "role text"),
            MaterializationEntry(
                kind=WriteKind.COPY_TREE,
                target=mirror,
                source=Path("/lib/skills/quality-gate"),
                tree_digest="ab" * 32,
            ),
        ),
        composition=replace(
            _composition(),
            skills=(Skill("skills::quality-gate", Path("/lib/skills/quality-gate"), "ab" * 32),),
        ),
    )

    (check,) = record["checks"]
    text = render_report(record)

    assert check["runs_from"] == str(mirror / "hooks" / "done-gate.sh")
    assert check["planned"] is True
    assert "rack:->done" in text and "quality-gate/hooks/done-gate.sh" in text
    assert "on_error=refuse  by ai-hats-gates" in text


def test_an_entry_shows_its_source_and_one_from_outside_the_composition_is_marked(tmp_path: Path):
    """A link into the person's home is a planning input, not a skill mirror;
    the reader must be able to tell the two apart without --json."""
    root = tmp_path / "cache"
    skill = tmp_path / "lib" / "skills" / "hatrack"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# h\n")
    home_entry = tmp_path / "home" / ".config" / "opencode" / "themes"
    record = _record(
        tmp_path,
        entries=(
            describe_mkdir(root),
            MaterializationEntry(
                kind=WriteKind.COPY_TREE,
                target=root / "skills" / "hatrack",
                source=skill,
                tree_digest="ef" * 32,
            ),
            describe_symlink(home_entry, root / "opencode" / "themes"),
            describe_write_text(root / "prompt.md", "role text"),
        ),
        composition=replace(_composition(), skills=(Skill("skills::hatrack", skill, "ef" * 32),)),
    )

    text = render_report(record)

    mirror, link, prompt = (
        line
        for line in text.splitlines()
        if line.startswith("  copy_tree")
        or line.startswith("  symlink")
        or line.startswith("  write_text")
    )
    assert f"<- {skill}" in mirror and "outside" not in mirror
    assert f"<- {home_entry}" in link and "outside the composition" in link
    assert "<-" not in prompt


def test_a_record_without_a_composition_carries_no_section(tmp_path: Path):
    """An old record read back from disk may predate the composition half."""
    record = _record(tmp_path)
    del record["composition"]

    assert "\ncomposition" not in render_report(record)
