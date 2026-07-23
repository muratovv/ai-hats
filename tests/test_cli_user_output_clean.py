import ast
import os
import re

import pytest


def test_no_internal_task_ids_in_user_facing_strings():
    """Ensure no HATS-xxx task IDs leak into Click help strings, CLI command docstrings, or console messages."""
    violations = []
    root_dir = os.path.join(os.path.dirname(__file__), "..")

    for dirpath, _, filenames in os.walk(os.path.join(root_dir, "src", "ai_hats")):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            filepath = os.path.join(dirpath, filename)
            relpath = os.path.relpath(filepath, root_dir)

            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            if "HATS-" not in content:
                continue

            try:
                tree = ast.parse(content, filename=filepath)
            except Exception:
                continue

            for node in ast.walk(tree):
                # 1. Click option/argument help= descriptions
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr

                    for kw in node.keywords:
                        if kw.arg in ("help", "description") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            if re.search(r"\bHATS-[0-9]+\b", kw.value.value):
                                violations.append((relpath, kw.value.lineno, f"help string: {kw.value.value!r}"))

                    # 2. Console prints / user warnings
                    if func_name in ("echo", "secho", "fail", "ClickException", "UsageError", "BadParameter"):
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and re.search(r"\bHATS-[0-9]+\b", arg.value):
                                violations.append((relpath, arg.lineno, f"{func_name}: {arg.value!r}"))

                # 3. Click CLI command docstrings
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    is_click = False
                    for dec in node.decorator_list:
                        dec_name = ""
                        if isinstance(dec, ast.Name):
                            dec_name = dec.id
                        elif isinstance(dec, ast.Attribute):
                            dec_name = dec.attr
                        elif isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Name):
                                dec_name = dec.func.id
                            elif isinstance(dec.func, ast.Attribute):
                                dec_name = dec.func.attr
                        if dec_name in ("command", "group", "cli"):
                            is_click = True
                            break
                    if is_click:
                        doc = ast.get_docstring(node)
                        if doc and re.search(r"\bHATS-[0-9]+\b", doc):
                            violations.append((relpath, node.lineno, f"command docstring: {doc!r}"))

    if violations:
        msg = "\n".join(f"  {v[0]}:{v[1]} - {v[2]}" for v in violations)
        pytest.fail(f"Found internal task IDs (HATS-xxx) in user-facing strings:\n{msg}")
