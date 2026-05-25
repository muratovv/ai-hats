"""HATS-501: pipeline funnel must preserve overlay content for sub-agent path.

Locks the contract that ``ComposeRole`` (and any future producer of the
``system_prompt`` funnel key) emits text containing every overlay-layer
contribution — not just the role's built-in composition. The wholesale
``with_injection_override`` mechanic inside ``SubAgentRunner._run_attempt``
means a partial funnel text silently replaces a fully-composed result;
this test guards the funnel input so that path is harmless.

Sister to ``test_funnel_value_contract.py`` — same HATS-452 contract
family (П1 in ADR-0005 / HATS-456 single-derivation-point invariant).
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.materialize import compose_for_role
from ai_hats.providers import ClaudeProvider


# Non-built-in w.r.t. ``maintainer.composition.traits`` so the trait-body
# markers below cannot be satisfied accidentally by maintainer's own
# built-in composition (cf. the brainstorm-card false-positive trap with
# ``dev::python``, which IS already built-in to maintainer).
GLOBAL_TRAIT = "dev::go-cli"
PROJECT_TRAIT = "dev::go-grpc"

# ``injection_append`` markers are author-controlled — pick distinct
# unlikely-to-collide strings. The ``add_trait`` markers are read at
# runtime from the trait's own injection body (see
# ``_trait_body_marker``) so this test does NOT content-couple to
# library files: if ``library/usage/traits/dev/go-cli/config.yaml``
# changes its injection text, the test re-reads the new text and
# continues to check that the overlay added trait's *current* body
# reaches the funnel.
INJ_GLOBAL = "GLB-INJ-MARKER-ZZZ"
INJ_PROJECT = "PRJ-INJ-MARKER-QQQ"


def _trait_body_marker(trait_name: str) -> str:
    """Return a substring of the trait's injection body to use as a
    presence marker — fresh-read from the library so the test doesn't
    break when the trait file is edited in unrelated work.
    """
    # Use a throwaway assembler to walk the resolver.
    asm = Assembler(Path.cwd())
    config = asm.resolver.resolve_trait_config(trait_name)
    assert config is not None, f"trait {trait_name!r} not resolvable"
    injection = (config.injection or "").strip()
    assert injection, f"trait {trait_name!r} has empty injection"
    # Take the first non-blank line as the marker — it's the trait's
    # most stable structural element (usually a Markdown heading).
    first_line = next(
        line for line in injection.splitlines() if line.strip()
    )
    assert len(first_line) > 4, (
        f"trait {trait_name!r} first-line marker too short to be unique: "
        f"{first_line!r}"
    )
    return first_line


def _setup_project_with_overlays(tmp_path: Path, monkeypatch) -> tuple[Path, dict[str, str]]:
    """Bootstrap a real project under tmp_path with global + project overlays.

    Uses the real ``ai-hats self init`` and ``ai-hats config customize``
    CLI commands via ``CliRunner`` so the layered customizations.yaml
    files are produced exactly as a human would produce them. A
    synthetic ``HOME`` keeps the test isolated from the developer's
    real ``~/.ai-hats/customizations.yaml``.

    Returns ``(project_dir, markers)`` where ``markers`` is the
    ``{channel: expected substring}`` map to assert against the funnel /
    prompt.md output. ``add_trait`` marker substrings are read freshly
    from the library (see ``_trait_body_marker``).

    Caveat (fixture-collision): the ``mock_runners`` fixture transitively
    requires ``project_dir`` from conftest, which already creates
    ``tmp_path / "proj"`` and chdirs into it. We deliberately work in
    ``tmp_path / "proj501"`` and chdir again — ``CliRunner.invoke``
    picks up the second chdir's cwd. If the conftest ``project_dir``
    fixture ever starts consuming its own ``proj/ai-hats.yaml`` during
    runner setup, this isolation breaks silently. Refactor of
    ``mock_runners`` to decouple from ``project_dir`` is tracked under
    HATS-506 (epic).
    """
    project = tmp_path / "proj501"
    project.mkdir()
    fake_home = tmp_path / "home"
    (fake_home / ".ai-hats").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project)

    runner = CliRunner()
    for argv in [
        ["self", "init", "-r", "maintainer", "-p", "claude", "--no-update"],
        [
            "config", "customize", "maintainer",
            "--global", "--injection-append", INJ_GLOBAL,
            "--global", "--add-trait", GLOBAL_TRAIT,
        ],
        [
            "config", "customize", "maintainer",
            "--injection-append", INJ_PROJECT,
            "--add-trait", PROJECT_TRAIT,
        ],
    ]:
        res = runner.invoke(main, argv)
        assert res.exit_code == 0, f"setup {argv}: {res.output}"

    markers = {
        "global  injection_append":   INJ_GLOBAL,
        "global  add_trait body":     _trait_body_marker(GLOBAL_TRAIT),
        "project injection_append":   INJ_PROJECT,
        "project add_trait body":     _trait_body_marker(PROJECT_TRAIT),
    }
    return project, markers


def test_subagent_funnel_carries_all_overlay_content(
    tmp_path: Path, monkeypatch, mock_runners,
) -> None:
    """``system_prompt_override`` reaching ``SubAgentRunner.run`` must
    include every overlay-layer contribution from both global + project
    layers.

    HATS-501 regression catcher — fails on revert of the
    ``ComposeRole`` → ``compose_for_role`` facade routing fix
    (``src/ai_hats/pipeline/steps/compose.py``: replace the
    ``compose_for_role(asm, role)`` call with the legacy
    ``composer.compose(role)`` form to reproduce the original bug).
    """
    project, markers = _setup_project_with_overlays(tmp_path, monkeypatch)

    pf = project / "p.txt"
    pf.write_text("ok")
    res = CliRunner().invoke(main, [
        "execute", "--batch", "-r", "maintainer", "--prompt", str(pf),
    ])
    assert res.exit_code == 0, res.output

    sub_calls = mock_runners["sub_calls"]
    assert len(sub_calls) == 1, sub_calls
    funnel_override = sub_calls[0].get("system_prompt_override") or ""

    missing = [
        f"{label!r} ({marker!r})"
        for label, marker in markers.items()
        if marker not in funnel_override
    ]
    assert not missing, (
        "HATS-501 regression: pipeline funnel dropped overlay content "
        f"before SubAgentRunner. Missing {len(missing)} channel(s): "
        f"{missing}\n\nfunnel_override head:\n{funnel_override[:400]!r}"
    )


def test_hitl_session_prompt_carries_all_overlay_content(
    tmp_path: Path, monkeypatch,
) -> None:
    """Lock-in counterpart: ``WrapRunner`` / ``ClaudeProvider.
    build_session_prompt`` already propagates overlay content (verified
    empirically during HATS-501 brainstorm).

    Assert it at the composer/provider boundary so a future runtime
    refactor can't silently regress HITL to match the broken Automate
    behaviour. No pipeline involvement — the contract is at
    ``compose_for_role`` + ``provider.build_session_prompt``.
    """
    project, markers = _setup_project_with_overlays(tmp_path, monkeypatch)

    asm = Assembler(project)
    result = compose_for_role(asm, "maintainer")
    args, _env = ClaudeProvider().build_session_prompt(
        project, result, "test-sid-501",
    )
    prompt_md = Path(args[1]).read_text()

    missing = [m for m in markers.values() if m not in prompt_md]
    assert not missing, (
        f"HITL prompt.md missing overlay markers {missing}; "
        f"prompt.md head:\n{prompt_md[:400]!r}"
    )
