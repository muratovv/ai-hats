"""Every declared path override, against the resolver that actually answers it.

Each declaration's LAST chain link is rendered and compared with what the
resolver returns in a process where none of these names is set.

Two links are prose, not path expressions, and get their own test instead of the
renderer: ``AI_HATS_LIBRARY_ROOT`` falls to wherever the library package is
installed, ``RACK_TASKS_DIR`` to a walk-up — a procedure, not a place. The four
foreign names state no default at all; that they cannot is asserted here.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable

import pytest

from ai_hats import env, migration_backup, paths
from ai_hats.paths import library as library_paths
from ai_hats.surfaces.codex import provider as codex_provider
from ai_hats.surfaces.hook_channel import project_dir_from
from ai_hats.surfaces.opencode import provider as opencode_provider
from ai_hats_core import safe_delete
from ai_hats_rack import cli_common, roots_registry
from ai_hats_rack.resolver import resolve_root

#: One declaration home per distribution — ai-hats-rack and ai-hats-core cannot
#: import ``ai_hats``, so a single tuple was never available to them.
DECLARED: dict[str, Any] = {
    override.name: override
    for home in (env.OVERRIDES, safe_delete.OVERRIDES, cli_common.OVERRIDES)
    for override in home
}

ROSTER = {
    "AI_HATS_USER_HOME",
    "AI_HATS_DIR",
    "AI_HATS_PROJECT_DIR",
    "AI_HATS_VENV",
    "AI_HATS_LIBRARY_ROOT",
    "AI_HATS_CACHE_HOME",
    "AI_HATS_TRASH_DIR",
    "AI_HATS_BUMP_BACKUP_DIR",
    "AI_HATS_CODEX_BASE_HOME",
    "AI_HATS_OPENCODE_CONFIG_HOME",
    "RACK_TASKS_DIR",
    "RACK_ROOTS_FILE",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "CODEX_HOME",
    "CODEX_SQLITE_HOME",
    "CLAUDE_CONFIG_DIR",
    "CLINE_DATA_DIR",
    "GEMINI_CONFIG_DIR",
}

#: The constant its reader spells, for every name whose reader is not an import
#: leaf and therefore spells it a second time.
READER_CONSTANTS = {
    "AI_HATS_BUMP_BACKUP_DIR": migration_backup.ENV_BACKUP_DIR,
    "AI_HATS_CODEX_BASE_HOME": codex_provider._ENV_CODEX_BASE_HOME,
    "AI_HATS_OPENCODE_CONFIG_HOME": opencode_provider._ENV_OPENCODE_CONFIG_HOME,
    "XDG_CONFIG_HOME": opencode_provider.ENV_XDG_CONFIG_HOME,
    "CODEX_HOME": codex_provider._ENV_CODEX_HOME,
    "CODEX_SQLITE_HOME": codex_provider._ENV_CODEX_SQLITE_HOME,
}

#: The resolver each declared default is a claim about, called with no relevant
#: variable set. ``project_dir`` is the clean fixture project.
PROBES: dict[str, Callable[[Path], Path]] = {
    "AI_HATS_USER_HOME": lambda project_dir: paths.user_home(),
    "AI_HATS_DIR": paths.ai_hats_dir,
    "AI_HATS_PROJECT_DIR": lambda project_dir: project_dir_from({}),
    "AI_HATS_VENV": paths.venv_path,
    "AI_HATS_CACHE_HOME": lambda project_dir: paths.cache_home(),
    "AI_HATS_TRASH_DIR": lambda project_dir: safe_delete._resolve_base()[0],
    "AI_HATS_BUMP_BACKUP_DIR": lambda project_dir: migration_backup._resolve_base()[0],
    "AI_HATS_CODEX_BASE_HOME": lambda project_dir: (
        codex_provider.CodexSurface._configured_base_home()
    ),
    "AI_HATS_OPENCODE_CONFIG_HOME": lambda project_dir: (
        opencode_provider.OpenCodeSurface()._base_config_home()
    ),
    "RACK_ROOTS_FILE": lambda project_dir: roots_registry.registry_path(),
}

#: Names whose last chain link is prose, with the test that covers them instead.
NOT_A_PATH_EXPRESSION = {
    "AI_HATS_LIBRARY_ROOT": "an install location — test_the_library_root_falls_to_the_installed_package",
    "RACK_TASKS_DIR": "a walk-up procedure — test_the_tasks_dir_falls_to_a_walk_up",
}

_CHAIN_SEP = ", else "


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A process with none of them set, a private ``HOME``, and a project.

    ``~/.codex`` is created because the codex resolver refuses a base home that
    is not an existing directory — the fixture supplies the world, never the
    answer.
    """
    for name in DECLARED:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    return project


def _render(default: str, project_dir: Path) -> Path | None:
    """The path a stated default promises on a clean environment, or ``None``
    when its last link is prose rather than a path expression."""
    link = default.rsplit(_CHAIN_SEP, 1)[-1].strip().replace("`", "")
    head, _, rest = link.partition("/")
    roots: dict[str, Callable[[], Path | None]] = {
        "~": Path.home,
        "$TMPDIR": lambda: Path(tempfile.gettempdir()),
        "<project_dir>": lambda: project_dir,
        "<cwd>": Path.cwd,
        # Composed from the other declaration rather than restated, so the two
        # cannot drift: <ai_hats_dir> IS whatever AI_HATS_DIR falls back to.
        "<ai_hats_dir>": lambda: _render(DECLARED["AI_HATS_DIR"].default, project_dir),
    }
    if head not in roots:
        return None
    root = roots[head]()
    return None if root is None else (root / rest if rest else root)


def _mismatch(override: Any, project_dir: Path) -> tuple[Path, Path] | None:
    """``(stated, resolved)`` when a declaration's default is not what the
    resolver answers, else ``None``."""
    stated = _render(override.default, project_dir)
    assert stated is not None, f"{override.name}: default is prose, not checkable here"
    resolved = PROBES[override.name](project_dir).resolve()
    return None if resolved == stated.resolve() else (stated, resolved)


def test_every_configurable_path_is_declared_exactly_once() -> None:
    """A name a generator cannot see is a name the reference page will not hold."""
    assert set(DECLARED) == ROSTER
    declared_count = sum(
        len(home) for home in (env.OVERRIDES, safe_delete.OVERRIDES, cli_common.OVERRIDES)
    )
    assert declared_count == len(ROSTER), "a name is declared in two distributions"


@pytest.mark.parametrize("name", sorted(READER_CONSTANTS))
def test_a_declared_name_is_the_one_its_reader_spells(name: str) -> None:
    """These names live in two modules by construction — the declaration home is
    an import leaf and their readers are not. A rename that reaches only one of
    the two spellings turns this red instead of silently declaring a dead name."""
    assert DECLARED[name].name == READER_CONSTANTS[name]


@pytest.mark.parametrize(
    "name", sorted(name for name, override in DECLARED.items() if not override.foreign)
)
def test_the_stated_default_is_what_a_clean_environment_answers(name: str, clean_env: Path) -> None:
    if name in NOT_A_PATH_EXPRESSION:
        pytest.skip(NOT_A_PATH_EXPRESSION[name])
    drift = _mismatch(DECLARED[name], clean_env)
    assert drift is None, f"{name} declares {drift[0]}, the resolver answers {drift[1]}"


def test_the_positive_control_a_wrong_default_is_actually_caught(clean_env: Path) -> None:
    """Without this, a green run above is indistinguishable from a comparison
    that never happened."""
    wrong = env.Override("AI_HATS_CACHE_HOME", "`~/.cache/not-ai-hats`", "deliberately wrong")
    assert _mismatch(wrong, clean_env) is not None
    assert _mismatch(DECLARED["AI_HATS_CACHE_HOME"], clean_env) is None


def test_every_declaration_is_checked_or_says_why_not() -> None:
    """The classification has to stay total: a new declaration lands in a probe,
    in the prose list with the test that covers it, or turns this red."""
    for name, override in DECLARED.items():
        if override.foreign:
            continue
        assert name in PROBES or name in NOT_A_PATH_EXPRESSION, f"{name} is checked by nothing"


def test_a_foreign_name_states_no_default() -> None:
    """We honour ``XDG_*`` / ``CODEX_*``; we do not define them. Stating a
    default for one would put our word on somebody else's contract, so the form
    refuses it — the doc line says where the name sits in our chain instead."""
    for name, override in DECLARED.items():
        assert override.foreign == (override.default == ""), name
        if override.foreign:
            assert override.doc, name


def test_the_library_root_falls_to_the_installed_package(clean_env: Path) -> None:
    """The last link of ``AI_HATS_LIBRARY_ROOT`` is an install location, so it is
    checked against the resolver that finds it, not against a rendered literal.

    What this does NOT cover: the two links above it (a source checkout above
    the project dir, or above the cwd). Both need a checkout laid out on disk,
    and a fixture that fakes one would be testing the fixture.
    """
    assert (
        library_paths.builtin_library_root(project_dir=clean_env, cwd=clean_env)
        == library_paths._importlib_library_root()
    )


def test_the_tasks_dir_falls_to_a_walk_up(clean_env: Path) -> None:
    """``RACK_TASKS_DIR`` declares a procedure, not a place: unset, the backlog
    is found by walking up from where the caller stands."""
    (clean_env / "ai-hats.yaml").write_text("", encoding="utf-8")
    nested = clean_env / "a" / "b"
    nested.mkdir(parents=True)
    assert (
        resolve_root(nested).tasks_dir
        == clean_env / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    )


def test_the_registry_path_rewrite_kept_the_old_answer(clean_env: Path) -> None:
    """``registry_path`` now composes its default from ``DEFAULT_ROOTS_FILE`` so
    the declaration and the resolver share one spelling; this pins the new
    expression to the ``Path.home() / ...`` one it replaced."""
    assert roots_registry.registry_path() == Path.home() / ".ai-hats" / "roots.yaml"


@pytest.mark.parametrize(
    "name, resolve, reader_sentinel",
    [
        ("AI_HATS_TRASH_DIR", safe_delete._resolve_base, safe_delete.HARD_DELETE_SENTINEL),
        (
            "AI_HATS_BUMP_BACKUP_DIR",
            migration_backup._resolve_base,
            migration_backup.HARD_DISABLE_SENTINEL,
        ),
    ],
)
def test_a_declared_sentinel_is_the_switch_its_reader_obeys(
    name: str,
    resolve: Callable[[], tuple[Path | None, bool]],
    reader_sentinel: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Path | Literal["-"]`` is the real type: set to the declared literal, the
    reader turns the behaviour OFF rather than treating it as a directory name."""
    sentinel = DECLARED[name].sentinel
    assert sentinel is not None and sentinel[0] == reader_sentinel
    monkeypatch.setenv(name, sentinel[0])
    assert resolve() == (None, True)


def test_only_the_two_dual_role_names_declare_a_pin() -> None:
    """``AI_HATS_DIR`` and ``AI_HATS_PROJECT_DIR`` are a user override AND what a
    spawner writes into a child, so they carry two defaults; the rest carry one."""
    assert sorted(name for name, o in DECLARED.items() if o.pin) == [
        "AI_HATS_DIR",
        "AI_HATS_PROJECT_DIR",
    ]


def test_the_pin_decides_which_of_the_two_defaults_is_in_force(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What ``pin`` claims, behaving: with a pin naming another project the
    ``AI_HATS_DIR`` beside it is a leaked session pin, not this project's
    override, and the resolver drops back to the declared default."""
    elsewhere = clean_env.parent / "other"
    elsewhere.mkdir()
    monkeypatch.setenv("AI_HATS_DIR", str(elsewhere / "base"))
    stated = _render(DECLARED["AI_HATS_DIR"].default, clean_env)

    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(clean_env))
    assert paths.ai_hats_dir(clean_env) == elsewhere / "base"

    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(elsewhere))
    with pytest.warns(UserWarning, match="leaked session pin"):
        assert paths.ai_hats_dir(clean_env) == stated
