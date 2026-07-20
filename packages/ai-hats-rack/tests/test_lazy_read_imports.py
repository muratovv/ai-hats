"""filelock (and its asyncio tail) stays off the read path (HATS-1072).

The lock is write-only — ``ls``/``context`` never take it — so the import must
live inside the write methods, not at module top level. Two guards: an AST pin
that filelock is imported only function-locally in the lock-owning modules, and
a clean-interpreter check that a read verb never pulls filelock/asyncio.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "ai_hats_rack"

#: The modules that acquire a file lock (write path only).
LOCK_MODULES = ("kernel.py", "docstore.py", "linked.py", "extensions/views.py")


def _module_level_imports(path: Path) -> list[str]:
    """Names imported at MODULE top level only (not inside a def/class)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:  # top-level statements only
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.If):  # e.g. `if TYPE_CHECKING:` — inspect its body
            for inner in node.body:
                if isinstance(inner, ast.ImportFrom) and inner.module:
                    found.append(inner.module)
    return found


def test_filelock_never_imported_at_module_level():
    """A regression pin: re-adding `from filelock import ...` at module top in a
    lock-owning module would drag filelock(+asyncio) back onto every read verb."""
    offenders = []
    for rel in LOCK_MODULES:
        # TYPE_CHECKING-only import is fine (no runtime cost); flag runtime top-level.
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or ""
                names = [a.name for a in node.names]
                if mod == "filelock" or "filelock" in names:
                    offenders.append(f"{rel}: runtime module-level filelock import")
    assert not offenders, "filelock must be function-local:\n" + "\n".join(offenders)


def test_ls_read_path_does_not_import_filelock(tmp_path):
    """End-to-end: a read verb in a clean interpreter never pulls filelock/asyncio."""
    code = textwrap.dedent(
        f"""
        import sys
        from ai_hats_rack.cli import main
        try:
            main(["ls", "--all", "--json", "--tasks-dir", {str(tmp_path)!r}],
                 standalone_mode=False)
        except SystemExit:
            pass
        assert "filelock" not in sys.modules, "filelock imported on the ls path"
        assert "asyncio" not in sys.modules, "asyncio imported on the ls path"
        """
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
