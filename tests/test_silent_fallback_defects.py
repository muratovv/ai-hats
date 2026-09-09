"""HATS-1373 — the defects the silent fallbacks were hiding.

The audit of 46 inert handlers turned up six where the silence was a bug, not a
decision. These are the two with user-visible consequences; both paths had no
test at all, which is why the silence survived.
"""

from __future__ import annotations


import pytest

from ai_hats.assembler import HealthStatus


class _BoomProvider:
    def system_prompt_path(self, layout):
        raise RuntimeError("provider is installed but broken")


def test_an_unresolvable_provider_reports_unknown_not_a_missing_key(monkeypatch, tmp_path):
    """`_check_health` used to drop the key, so the report read 'nothing to check'."""
    from ai_hats import assembler as assembler_mod

    class _Assembler:
        project_dir = tmp_path
        _check_health = assembler_mod.Assembler._check_health

        class project_config:  # noqa: N801 — stand-in for the config object
            provider = "not-installed"

    monkeypatch.setattr(
        assembler_mod, "get_surface", lambda name: (_ for _ in ()).throw(LookupError(name))
    )

    health = _Assembler._check_health(_Assembler(), result=None)

    assert health["system_prompt"] == HealthStatus.UNKNOWN


def test_a_provider_whose_prompt_path_raises_also_reports_unknown(monkeypatch, tmp_path):
    from ai_hats import assembler as assembler_mod

    class _Assembler:
        project_dir = tmp_path
        _check_health = assembler_mod.Assembler._check_health

        class project_config:  # noqa: N801
            provider = "broken"

    monkeypatch.setattr(assembler_mod, "get_surface", lambda name: _BoomProvider())

    health = _Assembler._check_health(_Assembler(), result=None)

    assert health["system_prompt"] == HealthStatus.UNKNOWN


def test_reinit_refuses_when_the_existing_config_will_not_load(monkeypatch, tmp_path):
    """It used to reset the project to claude in silence — an agy project included."""
    from ai_hats.cli import assembly
    from ai_hats.initialization import InitConfigUnreadableError
    from ai_hats.paths import PROJECT_CONFIG

    # The one Assembler of an init run is built at the root, before any step; that is
    # where a config that will not load has to refuse now.
    (tmp_path / PROJECT_CONFIG).write_text("provider: agy\n")
    monkeypatch.setattr(
        assembly,
        "_assembler",
        lambda project_dir: (_ for _ in ()).throw(ValueError("ai-hats.yaml is not valid YAML")),
    )

    with pytest.raises(InitConfigUnreadableError) as excinfo:
        assembly.AssemblerBootstrapper(tmp_path)

    assert "not valid YAML" in str(excinfo.value)


def test_the_subject_card_load_failure_is_logged(tmp_path, caplog):
    """A corrupt subject card used to be indistinguishable from 'this card has no links'."""
    from ai_hats.linked_context import load_linked_context

    card = tmp_path / "HATS-1" / "task.yaml"
    card.parent.mkdir(parents=True)
    card.write_text("id: [unclosed\n")

    with caplog.at_level("ERROR"):
        body = load_linked_context(tasks_root=tmp_path, ticket_id="HATS-1")

    assert body == ""
    assert any("subject card" in r.message for r in caplog.records), caplog.text
