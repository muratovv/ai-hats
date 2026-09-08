"""Read-only commands compose the library of the checkout you STAND in (HATS-1911).

The whole `list` group and `config status`'s role tree read the MAIN checkout,
so a role authored in a worktree came back "not found" and an existing one
reported a foreign tree's budget — silently. The write side stays asymmetric on
purpose (HATS-1127); the last test is the control that proves this file notices
if that moves.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.paths import builtin_library_root


def _populate_lib(lib: Path) -> Path:
    for layer in ("core", "usage"):
        (lib / layer).mkdir(parents=True)
    (lib / "core" / "pipelines").mkdir()
    return lib


def _make_lib(root: Path) -> Path:
    """The monorepo/worktree ai-hats-library layout; return its layer-root."""
    return _populate_lib(root / "packages" / "ai-hats-library" / "src" / "ai_hats_library")


def _fake_worktree(main_repo: Path, wt: Path) -> None:
    """Link ``wt`` to ``main_repo`` the way ``git worktree add`` does."""
    (main_repo / ".git" / "worktrees" / wt.name).mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main_repo / '.git' / 'worktrees' / wt.name}\n")


@pytest.fixture
def linked(tmp_path, monkeypatch):
    """A main checkout and a linked worktree, each with its own library."""
    main, wt = tmp_path / "main", tmp_path / "wt"
    main_lib, wt_lib = _make_lib(main), _make_lib(wt)
    _fake_worktree(main, wt)
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)
    return main, wt, main_lib, wt_lib


def test_assembler_prefer_cwd_composes_the_worktree_library(linked):
    """S1: the seam every read-only CLI call site rides."""
    main, wt, main_lib, wt_lib = linked

    read_only = Assembler(main, prefer_cwd=True, cwd=wt).library_paths
    writing = Assembler(main, cwd=wt).library_paths

    assert wt_lib / "core" in read_only
    assert main_lib / "core" not in read_only
    # the write path is unchanged — project_dir still decides (HATS-1127)
    assert main_lib / "core" in writing
    assert wt_lib / "core" not in writing


def _assembler_calls(module_path: Path) -> list[ast.Call]:
    """Every ``_assembler(...)`` call in ``module_path``."""
    tree = ast.parse(module_path.read_text())
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_assembler"
    ]


def _passes_prefer_cwd(call: ast.Call) -> bool:
    return any(
        kw.arg == "prefer_cwd" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    )


def test_every_list_command_prefers_cwd():
    """S2 as a guard, not five near-identical CliRunner tests.

    The whole module only lists — so a NEW subcommand that forgets ``prefer_cwd``
    is the regression worth catching, and it is one a per-command test would not
    catch because it would not exist yet.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "ai_hats" / "cli" / "list_cmd.py"
    calls = _assembler_calls(src)

    assert calls, f"no _assembler() calls found in {src} — the guard lost its subject"
    offenders = [c.lineno for c in calls if not _passes_prefer_cwd(c)]
    assert not offenders, (
        f"{src.name} lines {offenders}: a listing command must pass prefer_cwd=True, "
        "or it reports the MAIN checkout's library from inside a worktree (HATS-1911)"
    )


def test_config_status_prefers_cwd():
    """S3: the role tree follows cwd; ``set``/``do_bump`` in the same module do not."""
    src = Path(__file__).resolve().parents[1] / "src" / "ai_hats" / "cli" / "assembly.py"
    tree = ast.parse(src.read_text())
    status_fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "status"
    )
    calls = [
        n
        for n in ast.walk(status_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_assembler"
    ]

    assert len(calls) == 1, "config status builds exactly one assembler"
    assert _passes_prefer_cwd(calls[0])


def test_preview_does_not_invert_project_over_user_global(tmp_path, monkeypatch):
    """S4: the preview seam must layer like the session it previews.

    ``_project_context`` used to hand a whole path list to ``Assembler`` as
    ``extra``, appending a DUPLICATE of the builtin + user-global layers after
    the project's. Resolution is last-wins, so user-global then beat
    project-configured — inverting the documented order, and making
    ``show-prompt``/``--dry-run`` preview a composition the session would not
    produce.
    """
    from ai_hats.composition_seam import _project_context

    project = tmp_path / "proj"
    _make_lib(project)
    user_home = tmp_path / "home"
    (user_home / ".ai-hats").mkdir(parents=True)
    configured = tmp_path / "project-configured"
    configured.mkdir()
    (project / "ai-hats.yaml").write_text(f"library_paths:\n  - {configured}\n")
    monkeypatch.setenv("AI_HATS_USER_HOME", str(user_home))
    monkeypatch.delenv("AI_HATS_LIBRARY_ROOT", raising=False)

    paths = _project_context(project, None, prefer_cwd=True, cwd=project)[0].library_paths

    assert len(paths) == len(set(paths)), f"duplicated layers: {paths}"
    assert paths.index(configured) > paths.index(user_home / ".ai-hats"), (
        "project-configured must outrank user-global under last-wins resolution"
    )


def test_write_path_still_keys_off_project(linked):
    """Positive control for the absence claim 'write commands did not move'.

    If this fails, the write contract moved and every 'read-only only' claim in
    this file is worthless — the check exists to notice that, not to pass.
    """
    main, wt, main_lib, _wt_lib = linked
    assert builtin_library_root(main, cwd=wt) == main_lib
