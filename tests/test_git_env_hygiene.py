"""HATS-887 — lint: no unsanitized git-subprocess env in tests/ (+ conftest guard).

Flags a ``subprocess.*`` call whose command is a ``git`` list-literal AND whose
``env=`` is os.environ-derived without a ``GIT_*`` strip — the HATS-886 re-leak
that lets an ambient ``GIT_DIR`` retarget the call onto real ``.git``. ``env=``-less
git calls are covered by the autouse ``_isolate_git_env`` fixture (asserted here).
Known limit: an env built in a distant helper is the runtime repo-integrity
tripwire's job, not this static lint's. AST-walk precedent: ``test_import_hygiene.py``.
"""
from __future__ import annotations

import ast
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SUBPROCESS_FUNCS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
_PLUMBING_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def _is_subprocess_exec(call: ast.Call) -> bool:
    f = call.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr in _SUBPROCESS_FUNCS
        and isinstance(f.value, ast.Name)
        and f.value.id == "subprocess"
    )


def _command_is_git(call: ast.Call) -> bool:
    if not call.args:
        return False
    first = call.args[0]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        return isinstance(head, ast.Constant) and head.value == "git"
    return False


def _env_arg(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "env":
            return kw.value
    return None


def _refs_os_environ(node: ast.AST) -> bool:
    return any(
        isinstance(x, ast.Attribute)
        and x.attr == "environ"
        and isinstance(x.value, ast.Name)
        and x.value.id == "os"
        for x in ast.walk(node)
    )


def _is_git_plumbing_key(s: str) -> bool:
    # A GIT_* strip: `startswith("GIT_")`, a `pop("GIT_DIR")`, or a bare
    # `startswith("GIT")` — but NOT a "GITHUB_*" filter, which strips no plumbing.
    return s.startswith("GIT_") or s == "GIT"


def _has_git_strip(nodes: list[ast.AST]) -> bool:
    """True if any node strips a ``GIT``-prefixed key (startswith / pop / del).

    Deliberately does NOT treat a ``GIT_*`` string *set* as a dict key or
    subscript (``{**os.environ, "GIT_CONFIG_GLOBAL": ...}``) as a strip — that is
    the exact test_prepush re-leak shape we must still flag.
    """
    for node in nodes:
        for x in ast.walk(node):
            if (
                isinstance(x, ast.Call)
                and isinstance(x.func, ast.Attribute)
                and x.func.attr in ("startswith", "pop")
                and x.args
                and isinstance(x.args[0], ast.Constant)
                and isinstance(x.args[0].value, str)
                and _is_git_plumbing_key(x.args[0].value)
            ):
                return True
            if isinstance(x, ast.Delete):
                for tgt in x.targets:
                    if (
                        isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.slice, ast.Constant)
                        and isinstance(tgt.slice.value, str)
                        and _is_git_plumbing_key(tgt.slice.value)
                    ):
                        return True
    return False


def _env_related_nodes(func: ast.AST, env: ast.expr) -> list[ast.AST]:
    """The env expr plus, if it is a Name, its bindings + in-place mutations."""
    related: list[ast.AST] = [env]
    if not isinstance(env, ast.Name):
        return related
    name = env.id
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    related.append(node.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == name:
                related.append(node)  # e.g. env.pop("GIT_DIR", None)
        elif isinstance(node, ast.Delete):
            related.append(node)  # del env["GIT_DIR"]
    return related


def _module_offenders(tree: ast.Module) -> list[int]:
    """Line numbers of unsanitized git-subprocess env calls in one parsed tree."""
    offenders: list[int] = []
    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    scopes.append(tree)  # module-level calls too
    for scope in scopes:
        for call in [n for n in ast.walk(scope) if isinstance(n, ast.Call)]:
            if not (_is_subprocess_exec(call) and _command_is_git(call)):
                continue
            env = _env_arg(call)
            if env is None:
                continue
            related = _env_related_nodes(scope, env)
            if any(_refs_os_environ(n) for n in related) and not _has_git_strip(related):
                offenders.append(call.lineno)
    return sorted(set(offenders))


def _offends(source: str) -> bool:
    return bool(_module_offenders(ast.parse(source)))


# --------------------------------------------------------------------------- #


def test_no_unsanitized_git_subprocess_env_in_tests() -> None:
    """No test shells ``git`` with an os.environ-derived, GIT_*-unstripped env."""
    offenders: dict[str, list[int]] = {}
    for path in _TESTS_DIR.rglob("*.py"):
        lines = _module_offenders(ast.parse(path.read_text()))
        if lines:
            offenders[str(path.relative_to(_TESTS_DIR))] = lines
    assert not offenders, (
        "git-subprocess calls pass an os.environ-derived env without stripping "
        f"{list(_PLUMBING_VARS)} (HATS-886 re-leak): {offenders}. Strip via "
        "`{k: v for k, v in os.environ.items() if not k.startswith('GIT_')}` or "
        "drop the explicit env= (the autouse _isolate_git_env fixture covers it)."
    )


def test_detector_flags_releak_and_ignores_stripped() -> None:
    """Self-test: the detector FIRES on each re-leak shape and stays quiet on the
    sanctioned ones — so a green gate above means 'clean', not 'detector broken'."""
    # Re-leak shapes → flagged.
    assert _offends('subprocess.run(["git", "status"], env=os.environ.copy())')
    assert _offends('subprocess.run(["git", "status"], env={**os.environ})')
    assert _offends(
        'env = os.environ.copy()\n'
        'env["PYTHONPATH"] = "x"\n'
        'subprocess.run(["git", "x"], cwd=c, env=env)'
    )
    # test_prepush shape: sets GIT_CONFIG_* but does NOT strip the plumbing vars.
    assert _offends(
        'subprocess.run(["git", "push"], '
        'env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"})'
    )
    # A GITHUB_* filter strips no GIT_* plumbing var → still flagged (not a strip).
    assert _offends(
        'env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}\n'
        'subprocess.run(["git", "x"], cwd=c, env=env)'
    )

    # Sanctioned shapes → ignored.
    assert not _offends(
        'env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}\n'
        'subprocess.run(["git", "x"], cwd=c, env=env)'
    )
    assert not _offends(
        'env = os.environ.copy()\n'
        'env.pop("GIT_DIR", None)\n'
        'env.pop("GIT_WORK_TREE", None)\n'
        'env.pop("GIT_INDEX_FILE", None)\n'
        'subprocess.run(["git", "x"], cwd=c, env=env)'
    )
    assert not _offends('subprocess.run(["git", "x"], cwd=c)')  # no env= → conftest covers
    assert not _offends(  # non-git command → a stray GIT_* can't retarget it
        'subprocess.run(["ai-hats", "wt", "list"], env=os.environ.copy())'
    )


def test_isolate_git_env_fixture_intact() -> None:
    """The autouse ``_isolate_git_env`` conftest fixture — which the 285 no-env
    git call sites rely on — stays present, session/function autouse, and strips
    all three plumbing vars. RED-under-revert: weaken the fixture and this fails.
    """
    tree = ast.parse((_TESTS_DIR / "conftest.py").read_text())
    fixtures = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    }
    fn = fixtures.get("_isolate_git_env")
    assert fn is not None, "conftest lost the _isolate_git_env fixture"

    autouse = any(
        isinstance(d, ast.Call)
        and any(kw.arg == "autouse" and getattr(kw.value, "value", None) is True for kw in d.keywords)
        for d in fn.decorator_list
    )
    assert autouse, "_isolate_git_env must stay autouse"

    # The fixture deletes the vars in a `for var in (...)` loop, so assert both a
    # delenv call exists AND each plumbing var appears as a string constant in the
    # body (covers the loop-tuple and the literal-arg forms).
    calls_delenv = any(
        isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == "delenv"
        for x in ast.walk(fn)
    )
    assert calls_delenv, "_isolate_git_env must call monkeypatch.delenv"

    git_consts = {
        x.value
        for x in ast.walk(fn)
        if isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value.startswith("GIT")
    }
    missing = set(_PLUMBING_VARS) - git_consts
    assert not missing, f"_isolate_git_env stopped stripping {missing}"


def test_repo_integrity_tripwire_wired() -> None:
    """The session-scoped repo-integrity tripwire stays session-scoped + autouse —
    structural guard on the fixture wiring the pytester self-test exercises."""
    tree = ast.parse((_TESTS_DIR / "conftest.py").read_text())
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_real_repo_integrity_tripwire"),
        None,
    )
    assert fn is not None, "conftest lost the _real_repo_integrity_tripwire fixture"
    deco = next(
        (d for d in fn.decorator_list if isinstance(d, ast.Call)),
        None,
    )
    assert deco is not None, "tripwire must keep its @pytest.fixture(...) decorator"
    kw = {k.arg: k.value for k in deco.keywords}
    assert getattr(kw.get("autouse"), "value", None) is True, "tripwire must stay autouse"
    assert getattr(kw.get("scope"), "value", None) == "session", "tripwire must stay session-scoped"


def test_smoke_hook_strips_git_env_before_pytest() -> None:
    """The merge-smoke pre-commit hook strips GIT_* before spawning pytest — the
    root fix at the gate boundary where merge-smoke exports GIT_DIR at real .git.
    RED-under-revert: drop the ``env -u`` and this fails."""
    hook = (
        _TESTS_DIR.parent
        / "library/core/skills/git-mastery/git_hooks/pre-commit-smoke.sh"
    )
    text = hook.read_text()
    assert "pytest -m smoke" in text, "smoke hook no longer runs `pytest -m smoke`"
    for var in _PLUMBING_VARS:
        assert f"-u {var}" in text, f"smoke hook stopped stripping {var} before pytest"
