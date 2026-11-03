"""Part 3 of the migration: customizations become override files, and a
``- <composite>`` names the member someone else also brings."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "migrate_experimental.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60
    )


def test_a_subtracted_composite_names_the_shared_member(tmp_path: Path):
    root = tmp_path / "flat"
    root.mkdir()
    (root / "A.yaml").write_text("schema_version: 1\ntype: composite\nexpr: f1 + f2 + f3\n")
    (root / "B.yaml").write_text("schema_version: 1\ntype: composite\nexpr: f2 + f3 + f4\n")
    (root / "use.yaml").write_text("schema_version: 1\ntype: composite\nexpr: A + B - A\n")
    result = _run("--carried-away", str(root))
    assert result.returncode == 0, result.stderr
    assert "carried away: f2" in result.stdout
    assert "carried away: f3" in result.stdout


def _carried(stdout: str) -> set[str]:
    return {
        line.removeprefix("carried away: ")
        for line in stdout.splitlines()
        if line.startswith("carried away: ")
    }


def test_a_subtracted_composite_does_not_report_its_own_members(tmp_path: Path):
    """``f1`` is brought only by the composite being subtracted."""
    root = tmp_path / "flat"
    root.mkdir()
    (root / "A.yaml").write_text("schema_version: 1\ntype: composite\nexpr: f1 + f2 + f3\n")
    (root / "B.yaml").write_text("schema_version: 1\ntype: composite\nexpr: f2 + f3 + f4\n")
    (root / "use.yaml").write_text("schema_version: 1\ntype: composite\nexpr: A + B - A\n")
    result = _run("--carried-away", str(root))
    assert result.returncode == 0, result.stderr
    assert _carried(result.stdout) == {"f2", "f3"}


def test_a_nested_composite_counts_the_member_it_brings(tmp_path: Path):
    """``A`` brings ``f2`` through ``C``; subtracting ``A`` still carries ``f2`` away."""
    root = tmp_path / "flat"
    root.mkdir()
    (root / "C.yaml").write_text("schema_version: 1\ntype: composite\nexpr: f2\n")
    (root / "A.yaml").write_text("schema_version: 1\ntype: composite\nexpr: C + f1\n")
    (root / "B.yaml").write_text("schema_version: 1\ntype: composite\nexpr: f2\n")
    (root / "use.yaml").write_text("schema_version: 1\ntype: composite\nexpr: A + B - A\n")
    result = _run("--carried-away", str(root))
    assert result.returncode == 0, result.stderr
    assert _carried(result.stdout) == {"f2"}


def test_a_customization_removes_before_it_adds(tmp_path: Path):
    """Remove-then-add moves the name to the end; add-then-remove deletes it."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "customizations.yaml").write_text(
        "customizations:\n"
        "  maintainer:\n"
        "    add:\n"
        "      traits: [focus]\n"
        "    remove:\n"
        "      traits: [focus]\n"
    )
    out = tmp_path / "out"
    result = _run("--home", str(home), "--out", str(out))
    assert result.returncode == 0, result.stderr
    text = (out / "overrides" / "customize-maintainer.yaml").read_text()
    expr = text.split('expr: "', 1)[1].split('"', 1)[0]
    assert expr.index("- focus") < expr.index("+ focus")


def test_injection_append_becomes_a_prompt_on_the_override(tmp_path: Path):
    """Prose that was only an append is a prompt the override adds."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "customizations.yaml").write_text(
        "customizations:\n  maintainer:\n    injection_append: Remember the timer.\n"
    )
    out = tmp_path / "out"
    result = _run("--home", str(home), "--out", str(out))
    assert result.returncode == 0, result.stderr
    prompts = list(out.rglob("*.md"))
    assert prompts, result.stdout
    assert "Remember the timer." in prompts[0].read_text()
    text = (out / "overrides" / "customize-maintainer.yaml").read_text()
    assert "+ " in text


def test_an_empty_customization_is_printed(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "customizations.yaml").write_text("customizations:\n  maintainer: {}\n")
    out = tmp_path / "out"
    result = _run("--home", str(home), "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert "customization produced nothing: maintainer" in result.stdout
    assert not (out / "overrides").exists()


def test_a_project_customization_and_its_user_rules_are_translated(tmp_path: Path):
    """The project overlay lives in ``ai-hats.yaml``; its rules live under ``ai_hats_dir``."""
    home = tmp_path / "proj"
    rules = home / ".agent" / "ai-hats" / "user-rules"
    rules.mkdir(parents=True)
    (rules / "no-force-push.md").write_text("Do not force-push.\n")
    (home / "ai-hats.yaml").write_text(
        "ai_hats_dir: .agent/ai-hats\n"
        "default_role: assistant\n"
        "customizations:\n"
        "  maintainer:\n"
        "    add:\n"
        "      traits: [personal-workflow]\n"
    )
    out = tmp_path / "out"
    result = _run("--home", str(home), "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert "personal-workflow" in (out / "overrides" / "customize-maintainer.yaml").read_text()
    assert "no-force-push" in (out / "rules" / "no-force-push.md").read_text()
    assert "default: assistant" in (out / "ai-hats.yaml").read_text()


def test_an_empty_rule_directory_and_customization_are_printed(tmp_path: Path):
    home = tmp_path / "proj"
    (home / ".agent" / "ai-hats" / "user-rules").mkdir(parents=True)
    (home / "ai-hats.yaml").write_text("ai_hats_dir: .agent/ai-hats\ncustomizations: {}\n")
    out = tmp_path / "out"
    result = _run("--home", str(home), "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert "customization produced nothing" in result.stdout
    assert "user-rules produced nothing" in result.stdout


def test_dropins_keep_each_event_and_the_executable_bit(tmp_path: Path):
    home = tmp_path / "home"
    for event, body in (("pre-commit", "commit\n"), ("pre-push", "push\n")):
        script = home / ".githooks" / f"{event}.d" / "check.sh"
        script.parent.mkdir(parents=True)
        script.write_text(f"#!/bin/sh\n{body}")
        script.chmod(0o755)
    out = tmp_path / "out"
    result = _run("--home", str(home), "--out", str(out))
    assert result.returncode == 0, result.stderr
    commit = out / "hooks" / "dropin" / "pre-commit-check.sh"
    push = out / "hooks" / "dropin" / "pre-push-check.sh"
    assert commit.read_text() == "#!/bin/sh\ncommit\n"
    assert push.read_text() == "#!/bin/sh\npush\n"
    assert commit.stat().st_mode & 0o111
    assert push.stat().st_mode & 0o111
    assert "at: pre-commit" in (out / "hooks" / "dropin" / "pre-commit-check.yaml").read_text()
    assert (
        "run: pre-commit-check.sh"
        in (out / "hooks" / "dropin" / "pre-commit-check.yaml").read_text()
    )
    assert "at: pre-push" in (out / "hooks" / "dropin" / "pre-push-check.yaml").read_text()
    assert (
        "run: pre-push-check.sh" in (out / "hooks" / "dropin" / "pre-push-check.yaml").read_text()
    )
    expr = (out / "overrides" / "dropins.yaml").read_text()
    assert "+ pre-commit-check" in expr
    assert "+ pre-push-check" in expr


def test_customizations_become_overrides_and_an_unknown_role_is_named(tmp_path: Path):
    home = tmp_path / "home"
    (home / "user-rules").mkdir(parents=True)
    (home / ".githooks" / "pre-commit.d").mkdir(parents=True)
    (home / "customizations.yaml").write_text(
        "customizations:\n"
        "  maintainer:\n"
        "    add:\n"
        "      traits: [personal-workflow]\n"
        "  go-dev-full:\n"
        "    add:\n"
        "      traits: [personal-workflow]\n"
    )
    (home / "user-rules" / "no-force-push.md").write_text("Do not force-push.\n")
    (home / "library_paths.yaml").write_text("paths:\n  - ~/dev/ai-hats-custom\n")
    (home / "ai-hats.yaml").write_text("default_role: assistant\nactive_role: maintainer\n")
    (home / ".githooks" / "pre-commit.d" / "check.sh").write_text("#!/bin/sh\nexit 0\n")
    known = tmp_path / "known"
    known.mkdir()
    (known / "maintainer.yaml").write_text("schema_version: 1\ntype: composite\nexpr: base\n")
    out = tmp_path / "out"
    result = _run("--home", str(home), "--out", str(out), "--known", str(known))
    assert result.returncode == 0, result.stderr
    assert "unknown role: go-dev-full" in result.stdout
    text = (out / "overrides" / "customize-maintainer.yaml").read_text()
    assert 'apply: "^maintainer$"' in text
    assert "personal-workflow" in text
    assert "no-force-push" in (out / "rules" / "no-force-push.md").read_text()
    assert 'apply: ".*"' in (out / "overrides" / "user-rules.yaml").read_text()
    assert "~/dev/ai-hats-custom" in (out / "config.yaml").read_text()
    assert "default: assistant" in (out / "ai-hats.yaml").read_text()
    assert "active: maintainer" in (out / "local.yaml").read_text()
    assert "at: pre-commit" in (out / "hooks" / "dropin" / "pre-commit-check.yaml").read_text()
    first = {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in out.rglob("*")
        if path.is_file()
    }
    again = _run("--home", str(home), "--out", str(out), "--known", str(known))
    assert again.returncode == 0
    second = {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in out.rglob("*")
        if path.is_file()
    }
    assert first == second
