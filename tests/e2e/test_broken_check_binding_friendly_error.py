"""e2e (HATS-1541)

A broken ``checks:`` binding refuses typed, never with a traceback.

flow:   a developer starting a session on a role whose check binding is broken
cmds:
    ai-hats --dry-run
expect: a one-line typed refusal naming the binding, and no `Traceback` anywhere
why:    without it the composition path dumps 63 lines of stack, and the reader
        cannot tell a mistyped binding from a crash in ai-hats itself
"""

# Measured before the fix (HATS-1541, 2026-08-09 repro): `ai-hats --dry-run` on a
# role whose binding cannot be installed printed 63 lines of stack and exited 1.
# CheckBindingError was the one composition-path error missing from
# cli/_helpers._friendly_error_handlers(), so the single opt-out-proof rendering
# point (HATS-1228) never saw it.
#
# The binding used here names a skill the role does not compose — the
# _report_missing_skill arm of check_points. Deliberately NOT a bad point name:
# that arm is what ADR-0019 D11 moves out of ai-hats, and this test must keep
# proving the renderer after it is gone.
#
# Fail-under-revert: drop the CheckBindingError row from
# _friendly_error_handlers() and both params fail on the `Traceback` assertion.
# comment-length: allow — the measured baseline is the point of the test

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gates]

_ROLE_YAML = """\
name: broken
priorities:
  - Reliability
composition:
  traits: []
  rules: []
  skills: []
  apps:
    wt:
      - run: nowhere-skill/gate.sh
        at: [create]
        on_error: refuse
injection: |
  # ROLE: broken
"""


@pytest.fixture
def broken_binding_project(tmp_project):
    """``tmp_project`` plus a project-local library whose role binds a
    skill it does not compose — the cheapest unusable binding there is."""
    from ai_hats.models import ProjectConfig

    lib = tmp_project.path / "libraries"
    role_dir = lib / "roles" / "broken"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(_ROLE_YAML, encoding="utf-8")

    config = ProjectConfig.from_yaml(tmp_project.yaml)
    config.library_paths = [str(Path("libraries"))]
    config.default_role = "broken"
    config.save(tmp_project.yaml)
    return tmp_project


@pytest.mark.parametrize("argv", [("--dry-run",), ("--dry-run", "--role", "broken")])
def test_broken_binding_is_a_typed_refusal_not_a_traceback(
    broken_binding_project, argv: tuple[str, ...]
) -> None:
    result = broken_binding_project.run(*argv, timeout=60.0)

    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"CheckBindingError leaked a traceback ({combined.count(chr(10))} lines):\n{combined}"
    )
    assert result.exit_code != 0, f"a binding that cannot install must not pass:\n{combined}"
    # The message keeps the facts the composer had: who declared it, what it bound.
    for marker in ("checks:", "'broken'", "nowhere-skill"):
        assert marker in combined, f"missing {marker!r} in:\n{combined}"
