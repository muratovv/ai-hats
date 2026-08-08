from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


def test_assembler_accepts_injected_hooks_manager(tmp_path, monkeypatch):
    """A1: HooksManager is injectable, so Assembler can be tested with a fake/mock."""
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "home"))
    ProjectConfig(provider="claude").save(tmp_path / PROJECT_CONFIG)
    sentinel = object()
    asm = Assembler(tmp_path, hooks=sentinel)
    assert asm.hooks is sentinel


def test_default_hooks_manager_is_wired_when_not_injected(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "home"))
    ProjectConfig(provider="claude").save(tmp_path / PROJECT_CONFIG)
    asm = Assembler(tmp_path)
    from ai_hats.hooks_manager import HooksManager

    assert isinstance(asm.hooks, HooksManager)


def test_materialize_skips_when_binary_behind_source(tmp_path, monkeypatch):
    """HATS-1127: materialize() must refuse to write hooks if installed binary is behind upstream."""
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "home"))
    ProjectConfig(provider="claude").save(tmp_path / PROJECT_CONFIG)
    asm = Assembler(tmp_path)

    called = False

    def spy_install_git_hooks(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr(asm.hooks, "binary_behind_source", lambda: True)
    monkeypatch.setattr(asm.hooks, "install_git_hooks", spy_install_git_hooks)

    sink: list[str] = []
    asm.hooks.materialize(object(), warnings_sink=sink)

    assert not called, "expected materialize to skip when binary_behind_source() is True"
    assert any("behind upstream" in w for w in sink)
