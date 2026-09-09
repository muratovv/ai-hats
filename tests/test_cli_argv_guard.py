"""The argv contract for bare `ai-hats` (HATS-1932).

`_argv_guard.classify` is pure — no click, no I/O — so every row of the contract
is asserted directly here; `tests/test_cli_bare_positional_prompt.py` covers the
same contract through the real click group.
"""

from __future__ import annotations

import pytest

from ai_hats.cli._argv_guard import classify, command_paths

#: A stand-in for the real tree: one top-level name that also exists nested
#: (`list`), one purely nested (`init`), one leaf-only (`roles`).
PATHS = {
    "list": "list",
    "self": "self",
    "wt": "wt",
    "init": "self init",
    "roles": "list roles",
    "status": "config status",
}


def _refusal(*leftover: str, raw: tuple[str, ...] | None = None) -> str:
    msg = classify(list(raw if raw is not None else leftover), list(leftover), PATHS)
    assert msg is not None, f"{leftover!r} must be refused, not launched"
    return msg


def _allowed(*leftover: str, raw: tuple[str, ...] | None = None) -> None:
    msg = classify(list(raw if raw is not None else leftover), list(leftover), PATHS)
    assert msg is None, f"{leftover!r} must pass through, got refusal:\n{msg}"


def test_reserved_word_is_refused() -> None:
    """`ai-hats githooks` must not become a provider prompt (the reported defect)."""
    msg = _refusal("githooks")
    assert "githooks" in msg
    assert "python -m ai_hats.cli.githooks_hook" in msg, "must name where the capability went"


@pytest.mark.parametrize(
    ("word", "remedy"),
    [("task", "rack ls"), ("hyp", "rack ls --backlog hyp"), ("run", "ai-hats agent")],
)
def test_retired_surface_names_its_replacement(word: str, remedy: str) -> None:
    """A retired surface points at the command that replaced it, not just 'unknown'."""
    assert remedy in _refusal(word, "whatever")


def test_reserved_never_shadows_a_live_command() -> None:
    """Mounting a command named in RESERVED would make it unreachable.

    `classify` checks RESERVED before the tree, and only the click wiring keeps a
    real command from ever reaching it — so the two sets must not overlap.
    """
    from ai_hats.cli import main
    from ai_hats.cli._argv_guard import RESERVED

    clash = sorted(set(RESERVED) & set(command_paths(main)))
    assert not clash, f"RESERVED shadows live command(s): {clash}"


def test_module_remedy_names_an_importable_entry_point() -> None:
    """The one remedy pointing at a module must keep resolving.

    `githooks` is the single reserved word with a real handler; the other ten are
    retired, so there is nothing to bind their message to but this table.
    """
    import importlib
    import re

    from ai_hats.cli._argv_guard import RESERVED

    modules = re.findall(r"python -m ([\w.]+)", "\n".join(RESERVED.values()))
    assert modules, "the githooks remedy names a module — this test guards it"
    for name in modules:
        module = importlib.import_module(name)
        assert callable(getattr(module, "main", None)), f"{name} has no callable main()"


def test_every_ai_hats_remedy_resolves_in_the_real_tree() -> None:
    """A remedy naming a command that does not exist repeats the defect being fixed.

    Covers the `ai-hats …` spellings only; the `rack …` ones are another CLI's
    surface and stay unchecked here.
    """
    from ai_hats.cli import main
    from ai_hats.cli._argv_guard import RESERVED

    real = command_paths(main)
    for word, remedy in RESERVED.items():
        for line in remedy.splitlines():
            token = line.strip()
            if not token.startswith("ai-hats "):
                continue
            named = token.removeprefix("ai-hats ").strip("`.")
            assert real.get(named.split()[0]) == named, (
                f"RESERVED[{word!r}] points at `ai-hats {named}`, which is not a command"
            )


def test_help_flag_never_reaches_the_provider() -> None:
    """R1: `-h` alone is a user reaching for ai-hats' help, never a prompt."""
    msg = _refusal("-h")
    assert "-h" in msg
    assert "ai-hats -- -h" in msg, "must name the escape that forwards it deliberately"


def test_help_flag_after_prose_is_still_refused() -> None:
    """R1 holds even when the first token is not a known word."""
    assert "--help" in _refusal("hello", "--help")


def test_reserved_word_outranks_the_help_flag() -> None:
    """The reported invocation: the wrong *word* is the useful error, not the flag."""
    msg = _refusal("githooks", "--help")
    assert "githooks" in msg
    assert "githooks_hook" in msg


def test_nested_command_typed_at_top_level_names_its_real_path() -> None:
    """R2: the did-you-mean is the actual path, derived from the tree."""
    assert "ai-hats self init" in _refusal("init")
    assert "ai-hats list roles" in _refusal("roles")
    assert "ai-hats config status" in _refusal("status")


def test_top_level_name_is_not_refused() -> None:
    """A real top-level command reaches click's own routing untouched."""
    _allowed("list")
    _allowed("wt")


def test_prose_passes_through() -> None:
    """R3: the documented primary surface — unquoted and quoted prose alike."""
    _allowed("hello", "world")
    _allowed("fix the bug in cli.py")
    _allowed("deploy")


def test_double_dash_escapes_every_rule() -> None:
    """R4: `--` is the documented way to mean the word literally."""
    _allowed("githooks", "--help", raw=("--", "githooks", "--help"))
    _allowed("-h", raw=("--", "-h"))
    _allowed("init", raw=("--", "init"))


def test_bare_invocation_is_untouched() -> None:
    """No leftover at all is the primary surface: launch, never refuse."""
    _allowed()


class _Cmd:
    def __init__(self, commands=None):
        self.commands = commands or {}


def test_command_paths_prefers_the_shallower_name() -> None:
    """`list` is the top-level group even though `session list` also exists."""
    tree = _Cmd(
        {
            "session": _Cmd({"list": _Cmd(), "retro": _Cmd()}),
            "list": _Cmd({"roles": _Cmd()}),
        }
    )
    paths = command_paths(tree)
    assert paths["list"] == "list", "a nested namesake must not shadow the top-level command"
    assert paths["retro"] == "session retro"
    assert paths["roles"] == "list roles"
