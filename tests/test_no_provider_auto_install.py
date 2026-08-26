"""Nothing shipped may install a provider on the user's behalf (HATS-1826).

ai-hats used to reach for ``uv pip install`` when a surface name did not resolve
— from ``get_surface``, from the assembler's provider validation and from
``self_heal``. That capability is gone, third parties included: the
``ai_hats.surface_registry`` entry-point group stays open so anyone can declare and
ship a surface, but installing one is the user's job. ADR-0026 D3 — a bypass is
closed, not documented.

The defect is the installer VERB in a shipped module, not the word "surface":
what made the old path a bypass was ai-hats spawning an installer for something
it wanted to import. ``ALLOWED`` holds the calls that install something else,
each with its reason, and the second test keeps that list from going stale.
"""  # comment-length: allow — the gate's contract is the whole point of the file

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"

#: Installer verbs as argv fragments — the executable form, so a docstring that
#: merely mentions ``uv pip install`` is not a finding.
_INSTALL_VERBS = (("uv", "pip", "install"), ("pip", "install"), ("pip3", "install"))

#: ``module path -> what this call installs, since it is not a provider``.
ALLOWED: dict[str, str] = {
    "ai_hats/self_heal.py": "re-points a stale editable at a dir already in the tree",
    "ai_hats/cli/maintenance.py": "`self update` installs ai-hats itself",
    "ai_hats/_bootstrap.py": "heals ai-hats' own declared dependencies",
}


def _argv_literals(node: ast.AST) -> list[str]:
    """String literals of a list/tuple display, in order — argv as it is written."""
    if not isinstance(node, ast.List | ast.Tuple):
        return []
    return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def _spells_install(argv: list[str]) -> bool:
    return any(
        argv[i : i + len(verb)] == list(verb)
        for verb in _INSTALL_VERBS
        for i in range(len(argv) - len(verb) + 1)
    )


def _install_sites(text: str) -> list[int]:
    """Line numbers where a module spawns an installer.

    Two spellings, both executable: an argv display (wherever it is written — a
    ``subprocess.run([...])`` argument or a ``cmd = [...]`` a helper returns),
    and a command string handed to a call (the ``shell=True`` shape).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - an unparsable src/ file fails elsewhere
        return []
    hits: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.List | ast.Tuple) and _spells_install(_argv_literals(node)):
            hits.add(node.lineno)
        elif isinstance(node, ast.Call):
            for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if "pip install" in arg.value:
                        hits.add(node.lineno)
    return sorted(hits)


def test_no_shipped_module_installs_a_provider() -> None:
    """Every installer call under ``src/`` is on the allow-list, with its reason."""
    offenders: list[str] = []
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        rel = py_file.relative_to(SRC_DIR).as_posix()
        if rel in ALLOWED:
            continue
        offenders += [f"{rel}:{line}" for line in _install_sites(py_file.read_text("utf-8"))]

    assert not offenders, (
        f"shipped module(s) spawn an installer this gate does not know about: {offenders}. "
        "ai-hats does not install providers or surfaces (HATS-1826) — if the call "
        "installs something else, add the module to ALLOWED with the reason."
    )


def test_the_allow_list_is_not_stale() -> None:
    """Anti-vacuity: an allow-listed module that stopped installing must be dropped.

    Otherwise the gate keeps passing after the call is removed, and the entry
    reads as sanctioning an installer nobody has.
    """
    dead = [
        rel
        for rel in ALLOWED
        if not (SRC_DIR / rel).is_file() or not _install_sites((SRC_DIR / rel).read_text("utf-8"))
    ]
    assert not dead, f"ALLOWED names module(s) with no installer call left: {dead}"
