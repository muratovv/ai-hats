"""The root ``-m/--model`` alias, as the argv it produces (HATS-1891).

The wiring — that click parses ``-m`` as an option instead of leaking it into
the pass-through args, and that both the launch and the ``--dry-run`` report
read the same list — is pinned for real in
``tests/e2e/test_model_flag_hitl.py``. What is left here is the translation
itself, which is a pure function and needs no patching to observe.
"""

from __future__ import annotations

from ai_hats.cli._helpers import with_model_flag


def test_model_is_translated_to_the_provider_s_long_spelling() -> None:
    assert with_model_flag("fable", []) == ["--model", "fable"]


def test_model_precedes_the_user_s_own_provider_args() -> None:
    assert with_model_flag("fable", ["--resume"]) == ["--model", "fable", "--resume"]


def test_absent_model_adds_nothing() -> None:
    """No model means no flag — not an empty or ``None``-valued one."""
    assert with_model_flag(None, ["--resume"]) == ["--resume"]
    assert with_model_flag("", ["--resume"]) == ["--resume"]


def test_the_caller_s_list_is_not_mutated() -> None:
    """``ctx.args`` is reassigned, never appended to in place."""
    args = ["--resume"]
    with_model_flag("fable", args)
    assert args == ["--resume"]
