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

from ai_hats.cli import main


# Non-built-in w.r.t. ``maintainer.composition.traits`` so the trait-body
# markers cannot be satisfied accidentally by maintainer's own built-in
# composition (cf. the brainstorm-card false-positive trap with
# ``dev::python``, which IS already built-in to maintainer).
GLOBAL_TRAIT = "dev::go-cli"       # injection body contains "## GO CLI"
PROJECT_TRAIT = "dev::go-grpc"     # injection body contains "## GO gRPC"

MARKERS = {
    "global  injection_append":   "GLB-INJ-MARKER-ZZZ",
    "global  add_trait body":     "## GO CLI",
    "project injection_append":   "PRJ-INJ-MARKER-QQQ",
    "project add_trait body":     "## GO gRPC",
}


def _setup_project_with_overlays(tmp_path: Path, monkeypatch) -> Path:
    """Bootstrap a real project under tmp_path with global + project overlays.

    Uses the real ``ai-hats self init`` and ``ai-hats config customize``
    CLI commands via ``CliRunner`` so the layered customizations.yaml
    files are produced exactly as a human would produce them. A
    synthetic ``HOME`` keeps the test isolated from the developer's
    real ``~/.ai-hats/customizations.yaml``.
    """
    # Avoid colliding with the ``project_dir`` fixture (used by
    # ``mock_runners``) which already claims ``tmp_path / "proj"``.
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
            "--global", "--injection-append", MARKERS["global  injection_append"],
            "--global", "--add-trait", GLOBAL_TRAIT,
        ],
        [
            "config", "customize", "maintainer",
            "--injection-append", MARKERS["project injection_append"],
            "--add-trait", PROJECT_TRAIT,
        ],
    ]:
        res = runner.invoke(main, argv)
        assert res.exit_code == 0, f"setup {argv}: {res.output}"
    return project


def test_subagent_funnel_carries_all_overlay_content(
    tmp_path: Path, monkeypatch, mock_runners,
) -> None:
    """``system_prompt_override`` reaching ``SubAgentRunner.run`` must
    include every overlay-layer contribution from both global + project
    layers.

    HATS-501 regression catcher — fails on revert of the
    ``ComposeRole`` → ``compose_for_role`` facade routing fix.
    """
    project = _setup_project_with_overlays(tmp_path, monkeypatch)

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
        for label, marker in MARKERS.items()
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
    from ai_hats.assembler import Assembler
    from ai_hats.materialize import compose_for_role
    from ai_hats.providers import ClaudeProvider

    project = _setup_project_with_overlays(tmp_path, monkeypatch)

    asm = Assembler(project)
    result = compose_for_role(asm, "maintainer")
    args, _env = ClaudeProvider().build_session_prompt(
        project, result, "test-sid-501",
    )
    prompt_md = Path(args[1]).read_text()

    missing = [m for m in MARKERS.values() if m not in prompt_md]
    assert not missing, (
        f"HITL prompt.md missing overlay markers {missing}; "
        f"prompt.md head:\n{prompt_md[:400]!r}"
    )
