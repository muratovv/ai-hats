"""HATS-815 — leftover hook-sidecar scanner (detection-only).

Pure scan over library roots for skills whose ``metadata.yaml`` still carries
``git_hooks`` / ``runtime_hooks`` after the 814 frontmatter cutover.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.skill_sidecar import (
    LeftoverHookSidecar,
    leftover_sidecar_remedy,
    scan_leftover_hook_sidecars,
)


def _skill(root: Path, name: str, *, sidecar: str | None = None) -> Path:
    d = root / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: %s\n---\n# %s\n" % (name, name))
    if sidecar is not None:
        (d / "metadata.yaml").write_text(sidecar)
    return d


def test_scan_finds_git_hook_sidecar(tmp_path: Path) -> None:
    _skill(
        tmp_path,
        "demo",
        sidecar="name: demo\ngit_hooks:\n  pre-commit:\n    - git_hooks/check.sh\n",
    )
    findings = scan_leftover_hook_sidecars([tmp_path])
    assert len(findings) == 1
    assert findings[0].name == "demo"
    assert findings[0].keys == ("git_hooks",)
    assert isinstance(findings[0], LeftoverHookSidecar)


def test_scan_finds_runtime_hook_sidecar(tmp_path: Path) -> None:
    _skill(
        tmp_path,
        "guard",
        sidecar=(
            "name: guard\nruntime_hooks:\n  PreToolUse:\n"
            "    - matcher: Bash\n      script: h.sh\n"
        ),
    )
    findings = scan_leftover_hook_sidecars([tmp_path])
    assert [f.keys for f in findings] == [("runtime_hooks",)]


def test_scan_reports_both_hook_keys(tmp_path: Path) -> None:
    _skill(
        tmp_path,
        "both",
        sidecar=(
            "name: both\n"
            "git_hooks:\n  pre-commit:\n    - g.sh\n"
            "runtime_hooks:\n  PreToolUse:\n    - matcher: Bash\n      script: h.sh\n"
        ),
    )
    findings = scan_leftover_hook_sidecars([tmp_path])
    assert findings[0].keys == ("git_hooks", "runtime_hooks")


def test_scan_ignores_hookless_sidecar(tmp_path: Path) -> None:
    _skill(tmp_path, "plain", sidecar="name: plain\nauthor: ai-hats\ntags: [x]\n")
    assert scan_leftover_hook_sidecars([tmp_path]) == []


def test_scan_ignores_empty_hook_values(tmp_path: Path) -> None:
    # Present-but-falsy keys are not findings (nothing to migrate).
    _skill(tmp_path, "empty", sidecar="name: empty\ngit_hooks: {}\nruntime_hooks: []\n")
    assert scan_leftover_hook_sidecars([tmp_path]) == []


def test_scan_ignores_absent_sidecar(tmp_path: Path) -> None:
    _skill(tmp_path, "nofile")  # SKILL.md only, no metadata.yaml
    assert scan_leftover_hook_sidecars([tmp_path]) == []


def test_scan_root_without_skills_dir(tmp_path: Path) -> None:
    (tmp_path / "rules").mkdir()  # a library root that has no skills/ subdir
    assert scan_leftover_hook_sidecars([tmp_path]) == []


def test_scan_malformed_sidecar_no_finding(tmp_path: Path) -> None:
    # Detector is a heads-up, not a validator — malformed yaml yields no finding
    # (the compose-guard owns the loud path).
    _skill(tmp_path, "broken", sidecar="name: broken\ngit_hooks: [unclosed\n")
    assert scan_leftover_hook_sidecars([tmp_path]) == []


def test_scan_dedups_skill_reached_via_multiple_roots(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _skill(real, "dup", sidecar="name: dup\ngit_hooks:\n  pre-commit:\n    - g.sh\n")
    # Second root symlinks to the same skills/ tree.
    link_root = tmp_path / "link"
    link_root.mkdir()
    (link_root / "skills").symlink_to(real / "skills")
    findings = scan_leftover_hook_sidecars([real, link_root])
    assert len(findings) == 1


def test_scan_deterministic_order(tmp_path: Path) -> None:
    _skill(tmp_path, "b", sidecar="name: b\ngit_hooks:\n  pre-commit:\n    - g.sh\n")
    _skill(tmp_path, "a", sidecar="name: a\ngit_hooks:\n  pre-commit:\n    - g.sh\n")
    findings = scan_leftover_hook_sidecars([tmp_path])
    assert [f.name for f in findings] == ["a", "b"]


def test_remedy_names_skill_keys_and_frontmatter_key() -> None:
    msg = leftover_sidecar_remedy("demo", ("git_hooks",))
    assert "demo" in msg
    assert "git_hooks" in msg
    assert "ai_hats:" in msg
    assert "delete" in msg


def test_remedy_normalizes_list_and_tuple_identically() -> None:
    assert leftover_sidecar_remedy("demo", ["git_hooks"]) == leftover_sidecar_remedy(
        "demo", ("git_hooks",)
    )
