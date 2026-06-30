"""HATS-660 — py-security-lint declares its hook, is composed by dev::python, and
stays zero-egress.

Fail-under-revert wiring proof: drop the SKILL.md ``runtime_hooks`` block or the
``dev::python`` attachment and these go red. The materialization + settings.json
wiring itself is covered generically by ``test_assembler_runtime_hooks.py``.
"""
from pathlib import Path

import yaml

from ai_hats.models import RuntimeHook, SkillMetadata

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "library/usage/skills/py-security-lint"
HOOK = SKILL_DIR / "hooks/py_security_lint.py"
TRAIT_CFG = REPO_ROOT / "library/usage/traits/dev/python/config.yaml"


def test_declares_posttooluse_hook():
    meta = SkillMetadata.from_skill_dir(SKILL_DIR)
    post = meta.runtime_hooks.get("PostToolUse", [])
    assert (
        RuntimeHook(matcher="Edit|Write|MultiEdit", script="hooks/py_security_lint.py")
        in post
    ), f"py-security-lint must declare its PostToolUse hook; got {post!r}"


def test_hook_script_present_and_executable():
    assert HOOK.is_file()
    assert HOOK.stat().st_mode & 0o111, "hook must be executable"


def test_dev_python_trait_composes_skill():
    cfg = yaml.safe_load(TRAIT_CFG.read_text())
    skills = (cfg.get("composition") or {}).get("skills") or []
    assert "py-security-lint" in skills, f"dev::python must compose it; got {skills!r}"


def test_hook_is_zero_egress():
    src = HOOK.read_text()
    banned = (
        "import socket",
        "import urllib",
        "import http",
        "import ftplib",
        "import requests",
        "import httpx",
        "urllib.request",
    )
    hits = [b for b in banned if b in src]
    assert not hits, f"zero-egress violated — network surface in hook: {hits}"
