"""Layer order (ADR-0035 D3): overrides apply after every assignment, so a
lower layer's ``apply: ".*"`` still reaches a composite that exists only above."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.composition_seam import build_preview_payload
from ai_hats.diagnostics import Diagnostic
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.processing.errors import ProcessingError
from ai_hats.processing.expr import canonical, parse_expr
from ai_hats.processing.fold import expand_and_fold
from ai_hats.processing.names import merge
from ai_hats.processing.order import program
from ai_hats.processing.roots import Root, read_roots
from ai_hats.provenance import ComponentLayer
from ai_hats.surfaces.plan import TraceEntry
from tests._flat_library import write_root


def _prompt(text: str) -> str:
    return f"---\nschema_version: 1\ntype: prompt\nblock: body\n---\n{text}\n"


def _fold(roots: list[Path | Root], expr: str, diagnostics: list[Diagnostic] | None = None):
    sink: list[Diagnostic] = diagnostics if diagnostics is not None else []
    library = read_roots([r if isinstance(r, Root) else Root(r) for r in roots], sink)
    return expand_and_fold(program(library, merge(library, sink), parse_expr(expr)), expr, sink)


def test_a_lower_apply_all_reaches_a_composite_defined_only_above(tmp_path: Path):
    """A core ``apply: ".*"`` appends after the project's assignment of the role."""
    core = write_root(
        tmp_path / "core",
        {
            "arm.yaml": 'schema_version: 1\ntype: override\napply: ".*"\nexpr: "+ extra"\n',
            "extra.md": _prompt("EXTRA"),
        },
    )
    project = write_root(
        tmp_path / "project",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base\ntags: [role]\n",
            "base.md": _prompt("BASE"),
        },
    )
    folded = _fold([core, project], "role")
    assert folded.members == ("base", "extra")
    assert TraceEntry("extra", "arm", None) in folded.trace


def test_an_override_that_selects_nobody_warns(tmp_path: Path):
    """A stanza aimed at a role the library does not have is reported, and the plan still folds."""
    root = write_root(
        tmp_path / "lib",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base\n",
            "base.md": _prompt("BASE"),
            "gone.yaml": 'schema_version: 1\ntype: override\napply: "^go-dev-full$"\nexpr: "+ extra"\n',
            "extra.md": _prompt("EXTRA"),
        },
    )
    diagnostics: list[Diagnostic] = []
    folded = _fold([root], "role", diagnostics)
    assert folded.members == ("base",)
    assert [d.text for d in diagnostics] == ["gone selects no composite"]


def test_two_overrides_apply_in_layer_then_path_order(tmp_path: Path):
    """Layer index wins over the path, so a later root cannot jump ahead by its filename."""
    lower = write_root(
        tmp_path / "m-lower",
        {
            "z.yaml": 'schema_version: 1\ntype: override\napply: ".*"\nexpr: "+ from-lower"\n',
            "from-lower.md": _prompt("LOWER"),
        },
    )
    upper = write_root(
        tmp_path / "a-upper",
        {
            "a.yaml": 'schema_version: 1\ntype: override\napply: ".*"\nexpr: "+ from-upper"\n',
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base\n",
            "base.md": _prompt("BASE"),
            "from-upper.md": _prompt("UPPER"),
        },
    )
    folded = _fold([lower, upper], "role")
    assert folded.members == ("base", "from-lower", "from-upper")


def test_redefining_a_composite_replaces_its_tags(tmp_path: Path):
    """Tags come from the upper file whole; a lower ``hitl`` does not keep the override matching."""
    lower = write_root(
        tmp_path / "lower",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base\ntags: [hitl]\n",
            "base.md": _prompt("BASE"),
            "for-hitl.yaml": 'schema_version: 1\ntype: override\napply: "^hitl$"\nexpr: "+ extra"\n',
            "extra.md": _prompt("EXTRA"),
        },
    )
    upper = write_root(
        tmp_path / "upper",
        {"role.yaml": "schema_version: 1\ntype: composite\nexpr: base\ntags: [role]\n"},
    )
    diagnostics: list[Diagnostic] = []
    folded = _fold([lower, upper], "role", diagnostics)
    assert folded.members == ("base",)
    assert [d.text for d in diagnostics] == ["for-hitl selects no composite"]


def test_a_user_file_may_not_name_a_project_component(tmp_path: Path):
    """The same name from the project root resolves; from the user root it refuses."""
    project = write_root(
        tmp_path / "project",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base + secret\n",
            "base.md": _prompt("BASE"),
            "secret.md": _prompt("SECRET"),
        },
    )
    user = write_root(
        tmp_path / "user",
        {"mine.yaml": "schema_version: 1\ntype: composite\nexpr: secret\n"},
    )
    owned = _fold([Root(project, owner=ComponentLayer.PROJECT)], "role")
    assert owned.members == ("base", "secret")
    with pytest.raises(ProcessingError, match="owned by project"):
        _fold(
            [Root(user, owner=ComponentLayer.GLOBAL), Root(project, owner=ComponentLayer.PROJECT)],
            "mine",
        )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def test_a_worktree_library_is_project_for_the_chain(tmp_path: Path, monkeypatch):
    """``project_dir`` stays on main; the library the chain searches is outside it."""
    proj = tmp_path / "repo"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@t.co")
    _git(proj, "config", "user.name", "t")
    ProjectConfig(provider="claude", library_paths=[]).save(proj / PROJECT_CONFIG)
    _git(proj, "commit", "--allow-empty", "-m", "init", "--no-verify")
    wt = tmp_path / "linked"
    _git(proj, "worktree", "add", str(wt))
    write_root(
        wt / "libraries" / "experimental",
        {
            "holder.yaml": "schema_version: 1\ntype: composite\nexpr: proj-secret\n",
            "proj-secret.md": "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nSECRET\n",
        },
    )
    home = tmp_path / "home"
    monkeypatch.setenv("AI_HATS_USER_HOME", str(home))
    write_root(
        home / ".ai-hats" / "experimental",
        {"user-mine.yaml": "schema_version: 1\ntype: composite\nexpr: proj-secret\n"},
    )
    monkeypatch.chdir(wt)

    with pytest.raises(ProcessingError, match="owned by project"):
        build_preview_payload(proj, role="user-mine", experimental=True)
    held = build_preview_payload(proj, role="holder", experimental=True)
    assert "SECRET" in held.plan.prompt.text


def test_a_worktree_library_may_name_its_project_library_path(tmp_path: Path, monkeypatch):
    """A project ``library_paths`` root stays project when it is not repointed."""
    proj = tmp_path / "repo"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@t.co")
    _git(proj, "config", "user.name", "t")
    external = tmp_path / "external"
    write_root(
        external / "experimental",
        {"ext-secret.md": "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nEXTERNAL\n"},
    )
    (proj / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
        "library_paths:\n"
        f"  - {external}\n"
    )
    _git(proj, "commit", "--allow-empty", "-m", "init", "--no-verify")
    wt = tmp_path / "linked"
    _git(proj, "worktree", "add", str(wt))
    write_root(
        wt / "libraries" / "experimental",
        {"holder.yaml": "schema_version: 1\ntype: composite\nexpr: ext-secret\n"},
    )
    monkeypatch.chdir(wt)

    held = build_preview_payload(proj, role="holder", experimental=True)
    assert "EXTERNAL" in held.plan.prompt.text


def test_an_override_may_select_a_project_composite_without_naming_it(tmp_path: Path):
    """``apply`` is a selector, not a name, so a user file may arm a project role."""
    user = write_root(
        tmp_path / "user",
        {
            "arm.yaml": 'schema_version: 1\ntype: override\napply: ".*"\nexpr: "+ extra"\n',
            "extra.md": _prompt("EXTRA"),
        },
    )
    project = write_root(
        tmp_path / "project",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: base\n",
            "base.md": _prompt("BASE"),
        },
    )
    folded = _fold(
        [Root(user, owner=ComponentLayer.GLOBAL), Root(project, owner=ComponentLayer.PROJECT)],
        "role",
    )
    assert folded.members == ("base", "extra")


def _slots(root: Path, expr: str | None, *, default: str | None = None, active: str | None = None):
    library = read_roots([Root(root)], [])
    prog = program(
        library,
        merge(library, []),
        parse_expr(expr) if expr else None,
        default=parse_expr(default) if default else None,
        active=parse_expr(active) if active else None,
    )
    return prog, expand_and_fold(prog, canonical(prog.top), [])


def test_a_leading_sign_appends_to_active(tmp_path: Path):
    root = write_root(
        tmp_path / "lib",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: kept + dropped\n",
            "kept.md": _prompt("KEPT"),
            "dropped.md": _prompt("DROPPED"),
        },
    )
    prog, folded = _slots(root, "- dropped", active="role")
    assert canonical(prog.top) == "role - dropped"
    assert folded.members == ("kept",)
    assert TraceEntry("dropped", "role", removed_by="role - dropped") in folded.trace


def test_a_bare_expression_replaces_active_and_the_default(tmp_path: Path):
    root = write_root(
        tmp_path / "lib",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: kept\n",
            "bare.yaml": "schema_version: 1\ntype: composite\nexpr: other\n",
            "kept.md": _prompt("KEPT"),
            "other.md": _prompt("OTHER"),
        },
    )
    prog, folded = _slots(root, "bare", default="role", active="role")
    assert canonical(prog.top) == "bare"
    assert folded.members == ("other",)


def test_a_silent_cli_uses_active_and_otherwise_the_default(tmp_path: Path):
    root = write_root(
        tmp_path / "lib",
        {
            "role.yaml": "schema_version: 1\ntype: composite\nexpr: kept\n",
            "bare.yaml": "schema_version: 1\ntype: composite\nexpr: other\n",
            "kept.md": _prompt("KEPT"),
            "other.md": _prompt("OTHER"),
        },
    )
    active, _ = _slots(root, None, default="bare", active="role")
    assert canonical(active.top) == "role"
    default, _ = _slots(root, None, default="bare")
    assert canonical(default.top) == "bare"
    with pytest.raises(ProcessingError, match="needs an expression"):
        _slots(root, None)
