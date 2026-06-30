"""HATS-857 — worktree-isolation declares its PreToolUse gate, is composed by
trait-agent, and stays zero-egress.

Fail-under-revert wiring proof: drop the SKILL.md ``runtime_hooks`` block or the
``trait-agent`` attachment and these go red. The materialization + settings.json
wiring itself is covered generically by ``test_assembler_runtime_hooks.py``; the
hook's runtime behaviour is covered by ``tests/e2e/test_wt_gate_hook.py``.
"""
import importlib.util
import json
from pathlib import Path

import yaml

from ai_hats.models import RuntimeHook, SkillMetadata

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "library/core/skills/worktree-isolation"
HOOK = SKILL_DIR / "hooks/wt_gate.py"
EXTS_JSON = SKILL_DIR / "hooks/code_extensions.json"
TRAIT_CFG = REPO_ROOT / "library/core/traits/trait-agent/config.yaml"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("wt_gate", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_declares_pretooluse_hook():
    meta = SkillMetadata.from_skill_dir(SKILL_DIR)
    pre = meta.runtime_hooks.get("PreToolUse", [])
    assert (
        RuntimeHook(matcher="Edit|Write|MultiEdit", script="hooks/wt_gate.py") in pre
    ), f"worktree-isolation must declare its PreToolUse gate; got {pre!r}"


def test_hook_script_present_and_executable():
    assert HOOK.is_file()
    assert HOOK.stat().st_mode & 0o111, "hook must be executable"


def test_trait_agent_composes_skill():
    cfg = yaml.safe_load(TRAIT_CFG.read_text())
    skills = (cfg.get("composition") or {}).get("skills") or []
    assert "worktree-isolation" in skills, f"trait-agent must compose it; got {skills!r}"


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


def test_extensions_json_is_grouped_by_language():
    data = json.loads(EXTS_JSON.read_text())
    langs = {k: v for k, v in data.items() if not k.startswith("_")}
    assert langs, "code_extensions.json must list at least one language group"
    for lang, exts in langs.items():
        assert isinstance(exts, list) and exts, f"{lang} must be a non-empty list"
        assert all(
            isinstance(e, str) and e.startswith(".") for e in exts
        ), f"{lang} extensions must be dotted strings; got {exts!r}"
    # Config files are in scope (HATS-857 review): concurrent main-checkout edits
    # of shared config corrupt each other.
    assert ".yaml" in langs.get("config", []), "config group must include .yaml"


def test_embedded_default_mirrors_json():
    """The embedded _DEFAULT_LANGS fallback must match the shipped JSON, so the
    flattened library/hooks/ copy (which loses the sibling file) behaves the same."""
    mod = _load_hook_module()
    embedded = frozenset(e for exts in mod._DEFAULT_LANGS.values() for e in exts)
    from_json = mod._read_exts_json(EXTS_JSON)
    assert from_json == embedded, (
        "code_extensions.json and wt_gate._DEFAULT_LANGS drifted; "
        f"json-only={from_json - embedded}, embedded-only={embedded - from_json}"
    )
