"""The home for an environment variable name the ``ai_hats`` package reads (HATS-1414).

A name spelled here and imported is a name a reader can find: one edit renames
it, and this file is the register to scan. It is the first place a new one goes.

The claim used to be "all ``os.environ`` variables", which no edit could have
made true — so it said nothing, and a plan item asking where to register
``AI_HATS_HOOK_TIMEOUT_S`` was closed as having no answer while this file sat
here (HATS-1858 §23, HATS-1868). What holds instead:

* A **sibling distribution** — ``ai-hats-rack``, ``ai-hats-core``,
  ``ai-hats-observe``, ``ai-hats-library`` — does not depend on ``ai_hats`` and
  cannot import this module. Its names live with it, and no rule here reaches
  them.
* Inside this package, what is spelled **elsewhere** is a closed list, held by
  ``tests/test_env_homes.py``: it refuses a literal in any module the list does
  not already name. Growing the sprawl is now an edit somebody reviews.

Dependencies are restricted to standard library (``os``) to maintain absolute
leaf module purity (contract guarded by ``test_import_hygiene.py``).
"""

from __future__ import annotations

import os

# Env-var names read across ai-hats (HATS-917, HATS-1414)
ENV_AI_HATS_USER_HOME = "AI_HATS_USER_HOME"
ENV_AI_HATS_DIR = "AI_HATS_DIR"
AI_HATS_PROJECT_DIR_ENV = "AI_HATS_PROJECT_DIR"
ENV_AI_HATS_VENV = "AI_HATS_VENV"
ENV_LIBRARY_ROOT = "AI_HATS_LIBRARY_ROOT"
ENV_AI_HATS_CACHE_HOME = "AI_HATS_CACHE_HOME"
ENV_XDG_CACHE_HOME = "XDG_CACHE_HOME"
ENV_SESSION_CACHE_DIR = "AI_HATS_SESSION_CACHE_DIR"

# Session identity written at spawn (ADR-0025 D1). `AI_HATS_SESSION_ID` is
# absent on purpose — its home is `ai_hats_observe.trace` (HATS-948).
ENV_ROLE = "AI_HATS_ROLE"
ENV_ROOT_PID = "AI_HATS_ROOT_PID"
#: The session process's own interpreter. A second pin beside ENV_AI_HATS_VENV
#: because the agy global hook is invoked by the surface, not by our launcher.
ENV_AI_HATS_PYTHON = "AI_HATS_PYTHON"

#: Where this session's resident hook dispatcher listens.
ENV_HOOK_SOCKET = "AI_HATS_HOOK_SOCK"

# Hook-point vocabulary, owned by ADR-0020 D2; named here so it has one home.
ENV_HOOK_POINT = "AI_HATS_HOOK_POINT"
ENV_IN_HOOK = "AI_HATS_IN_HOOK"
ENV_FORCE = "AI_HATS_FORCE"
ENV_TASK_ID = "AI_HATS_TASK_ID"
ENV_WORKTREE_PATH = "AI_HATS_WORKTREE_PATH"
ENV_TASKS_DIR = "AI_HATS_TASKS_DIR"  # NOT rack's own RACK_TASKS_DIR
# Set only once the worktree is gone AND its branch reached the base branch, so
# it is the one signal separating "brought no code" from "already merged".
ENV_MERGED_SHA = "AI_HATS_MERGED_SHA"
#: The call envelope — per-CALL facts as one versioned JSON object,
#: BESIDE the scalars above, which shell keeps reading.
ENV_HOOK_CALL = "AI_HATS_HOOK_CALL"

# A budget and a hatch per gate channel — the tool-call one, then git's. Side by
# side because they ARE one pair spelled twice, and a reader comparing them had
# to open two dispatchers to see that.
ENV_HOOK_TIMEOUT_S = "AI_HATS_HOOK_TIMEOUT_S"
ENV_GATE_BROKEN_ACK = "AI_HATS_GATE_BROKEN_ACK"
ENV_GIT_HOOK_TIMEOUT_S = "AI_HATS_GIT_HOOK_TIMEOUT_S"
ENV_GIT_GATE_BROKEN_ACK = "AI_HATS_GIT_GATE_BROKEN_ACK"

#: The event a hook was called for, handed to the child.
ENV_HOOK_EVENT = "AI_HATS_HOOK_EVENT"
#: Handed to a surface whose config asset is copied verbatim, so the number in
#: it can still track the tool-call budget above.
ENV_HOOK_SURFACE_TIMEOUT_MS = "AI_HATS_HOOK_SURFACE_TIMEOUT_MS"
#: What the tool-call budget was called while only one surface offered it. Still
#: honoured, and saying so needs the old name to survive somewhere.
ENV_RETIRED_AGY_HOOK_TIMEOUT_S = "AI_HATS_AGY_HOOK_TIMEOUT_S"

ENV_WT_HOOK_TIMEOUT_S = "AI_HATS_WT_HOOK_TIMEOUT_S"
ENV_PTY_GRACE_S = "AI_HATS_PTY_GRACE_S"
ENV_PTY_TERM_S = "AI_HATS_PTY_TERM_S"
ENV_PIPELINE_KEEP_N = "AI_HATS_PIPELINE_KEEP_N"
ENV_STARTUP_HOLD = "AI_HATS_STARTUP_HOLD"


def _read(name: str) -> str | None:
    """Read environment variable; empty string is treated as unset (None)."""
    return os.environ.get(name) or None


class Budget:
    """A numeric knob: its name, its default, and the line a reader needs.

    Hand-written rather than a dataclass because this leaf is imported by every
    hook dispatcher and `dataclasses` costs ~7 ms alone; the dispatchers happen
    to pay it on another edge today, and that is a debt to borrow, not to owe.
    """

    __slots__ = ("name", "default", "doc")

    def __init__(self, name: str, default: float, doc: str) -> None:
        self.name = name
        self.default = default
        self.doc = doc


def read_budget(budget: Budget, environ: dict[str, str] | None = None) -> float:
    """``budget.default`` unless the environment holds a usable value.

    Usable means: parses as the default's own type, positive, and finite.
    Anything else falls back — a typo must never disarm a bound nor raise, which
    is the contract five of the six readers this replaces already kept.

    ``environ`` is typed as ``dict`` for the builtin alone: naming ``Mapping``
    here would import ``collections.abc``, 818 µs on a path measured in hundreds.
    """
    raw = (os.environ if environ is None else environ).get(budget.name)
    if not raw or not raw.strip():
        return budget.default
    try:
        value = type(budget.default)(raw)
    except (TypeError, ValueError):
        return budget.default
    if value <= 0 or value != value or value == float("inf"):
        return budget.default
    return value


#: Every numeric knob this package reads, each with the ONE copy of its default.
#: A call site takes the number from here rather than agreeing with it: agreement
#: is what drifts, and for a number a stale doc page is worse than none.
BUDGETS: tuple[Budget, ...] = (
    Budget(
        ENV_HOOK_TIMEOUT_S,
        60.0,
        "Seconds the whole hook chain for one tool call may spend.",
    ),
    Budget(
        ENV_GIT_HOOK_TIMEOUT_S,
        900.0,
        "Seconds one git-hook gate script may spend.",
    ),
    Budget(
        ENV_WT_HOOK_TIMEOUT_S,
        45.0,
        "Seconds one worktree lifecycle hook may ask for; the caller's lock caps it.",
    ),
    Budget(
        ENV_PTY_GRACE_S,
        5.0,
        "Seconds a PTY child is given to exit before it is signalled.",
    ),
    Budget(
        ENV_PTY_TERM_S,
        2.0,
        "Seconds between SIGTERM and SIGKILL during PTY teardown.",
    ),
    Budget(
        ENV_PIPELINE_KEEP_N,
        10,
        "Sibling pipeline-run directories kept before the oldest are pruned.",
    ),
    Budget(
        ENV_STARTUP_HOLD,
        10.0,
        "Seconds a startup warning is held on screen; 0 disables the hold.",
    ),
)

(
    HOOK_TIMEOUT,
    GIT_HOOK_TIMEOUT,
    WT_HOOK_TIMEOUT,
    PTY_GRACE,
    PTY_TERM,
    PIPELINE_KEEP_N,
    STARTUP_HOLD,
) = BUDGETS


#: Every configurable path this package resolves through the environment, plus
#: the foreign names it honours, each with the ONE description of how it falls
#: back. Names read by a sibling distribution are declared there, not here.
OVERRIDES: tuple[dict, ...] = (
    {
        "name": ENV_AI_HATS_USER_HOME,
        "default": "`~`",
        "doc": (
            "Home for ai-hats-managed global state; `HOME` stays intact, so tool auth still "
            "resolves."
        ),
    },
    {
        "name": ENV_AI_HATS_DIR,
        "default": "yaml `ai_hats_dir`, else `<project_dir>/.agent/ai-hats`",
        "doc": "The base dir holding the tracker, the library mirror and session state.",
        "pin": "the session's base dir, written at spawn; `paths` honours it as an override only while `AI_HATS_PROJECT_DIR` names this project, and drops the pair when it names another",
    },
    {
        "name": AI_HATS_PROJECT_DIR_ENV,
        "default": "`<cwd>`",
        "doc": "Which project a gate subprocess must inspect.",
        "pin": "the project the session was launched for; it is what decides whether the `AI_HATS_DIR` / `AI_HATS_VENV` beside it are this project's override or a leaked pin",
    },
    {
        "name": ENV_AI_HATS_VENV,
        "default": "yaml `venv_path`, else the managed `versions/<sha>`, else `<ai_hats_dir>/.venv`",
        "doc": (
            "The interpreter ai-hats runs itself and its hooks with; pair-scoped like "
            "`AI_HATS_DIR`."
        ),
    },
    {
        "name": ENV_LIBRARY_ROOT,
        "default": (
            "a source checkout above `<project_dir>` or `<cwd>`, else the installed library package"
        ),
        "doc": (
            "Where the builtin library is composed FROM — not the materialized mirror under "
            "`.agent`."
        ),
    },
    {
        "name": ENV_AI_HATS_CACHE_HOME,
        "default": "`$XDG_CACHE_HOME`/ai-hats, else `~/.cache/ai-hats`",
        "doc": (
            "The BASE of the machine-only cache class; the per-project key is always appended to "
            "it."
        ),
    },
    {
        "name": "AI_HATS_BUMP_BACKUP_DIR",
        "default": "`$TMPDIR`/ai-hats/bump-backups",
        "doc": "Where the pre-bump snapshot of the ai-hats-managed surface is written.",
        "sentinel": ("-", "take no snapshot at all — one stderr WARN per call"),
    },
    {
        "name": "AI_HATS_CODEX_BASE_HOME",
        "default": "`$CODEX_HOME`, else `~/.codex`",
        "doc": "The user's real codex home, projected into each session home.",
    },
    {
        "name": "AI_HATS_OPENCODE_CONFIG_HOME",
        "default": "`$XDG_CONFIG_HOME`, else `~/.config`",
        "doc": "The user's real config BASE — not the `opencode/` dir inside it.",
    },
    {
        "name": ENV_XDG_CACHE_HOME,
        "default": "",
        "doc": (
            "Platform cache base. Ranks under `AI_HATS_CACHE_HOME`, over `~/.cache`; we append "
            "`ai-hats/`."
        ),
        "foreign": True,
    },
    {
        "name": "XDG_CONFIG_HOME",
        "default": "",
        "doc": (
            "Platform config base. Ranks under `AI_HATS_OPENCODE_CONFIG_HOME`, over `~/.config`; "
            "the opencode child is given a session-scoped one instead."
        ),
        "foreign": True,
    },
    {
        "name": "CODEX_HOME",
        "default": "",
        "doc": (
            "Codex's own home. Ranks under `AI_HATS_CODEX_BASE_HOME`, over `~/.codex`; the codex "
            "child is given the session home instead."
        ),
        "foreign": True,
    },
    {
        "name": "CODEX_SQLITE_HOME",
        "default": "",
        "doc": (
            "Codex's rollout database. Unset, the codex base home serves; the codex child is "
            "given a session-scoped one instead."
        ),
        "foreign": True,
    },
    {
        "name": "CLAUDE_CONFIG_DIR",
        "default": "",
        "doc": (
            "Claude Code's own home, where its settings and transcripts are read from. Unset, "
            "`~/.claude` serves."
        ),
        "foreign": True,
    },
    {
        "name": "CLINE_DATA_DIR",
        "default": "",
        "doc": (
            "Cline's own home, where its session transcripts are read from. Unset, `~/.cline` "
            "serves; the cline child is given one explicitly."
        ),
        "foreign": True,
    },
    {
        "name": "GEMINI_CONFIG_DIR",
        "default": "",
        "doc": (
            "The agy/gemini home, where that surface's settings and brain dir are read from. "
            "Unset, `~/.gemini` serves."
        ),
        "foreign": True,
    },
)


def user_home_override() -> str | None:
    """Read ``AI_HATS_USER_HOME`` env var.

    Meaning: Runtime override for the global user home directory (bypassing ``Path.home()``)
    for ai-hats managed global state (e.g. global user config and global library layer).
    Documentation: ``docs/how-to-configure.md`` (Global configuration).
    """
    return _read(ENV_AI_HATS_USER_HOME)


def ai_hats_dir_override() -> str | None:
    """Read ``AI_HATS_DIR`` env var.

    Meaning: Runtime override for the framework base directory (by default ``.agent/ai-hats``).
    Pair-scoped with ``AI_HATS_PROJECT_DIR`` to prevent leaked session pins across projects.
    Documentation: ``docs/how-to-configure.md`` (Directory resolution, HATS-897).
    """
    return _read(ENV_AI_HATS_DIR)


def project_dir_pin() -> str | None:
    """Read ``AI_HATS_PROJECT_DIR`` env var.

    Meaning: Project root pin set at session spawn alongside ``AI_HATS_DIR`` to validate
    override scoping and ignore foreign leaked environment variables.
    Documentation: HATS-897 (Leaked session pin guard).
    """
    return _read(AI_HATS_PROJECT_DIR_ENV)


def venv_override() -> str | None:
    """Read ``AI_HATS_VENV`` env var.

    Meaning: Absolute path runtime override for the Python virtual environment location.
    Documentation: ``docs/how-to-configure.md`` (Python environment resolution, HATS-334).
    """
    return _read(ENV_AI_HATS_VENV)


def library_root_override() -> str | None:
    """Read ``AI_HATS_LIBRARY_ROOT`` env var.

    Meaning: Environment override for the builtin library root directory containing
    the ``core`` and ``usage`` composition layers.
    Documentation: ``docs/architecture.md`` (Builtin library resolution, HATS-831).
    """
    return _read(ENV_LIBRARY_ROOT)


def tool_home_override(env_var: str) -> str | None:
    """Read arbitrary tool home environment variable ``env_var``.

    Meaning: Generic environment override for tool-specific home directory pattern (``~/.<name>``).
    Documentation: Shared transcript-discovery and tool-home resolution (HATS-1087).
    """
    return _read(env_var)


def cache_home_override() -> str | None:
    """Read ``AI_HATS_CACHE_HOME`` env var.

    Meaning: Runtime override for the BASE of the machine-only cache class, which lives
    outside the project. Never a project's final cache root — ``paths.cache_root`` always
    appends the per-project key, so a leaked value cannot merge two projects' caches.
    Documentation: ``docs/ARCHITECTURE.md`` (Materialization), HATS-1398.
    """
    return _read(ENV_AI_HATS_CACHE_HOME)


def xdg_cache_home() -> str | None:
    """Read ``XDG_CACHE_HOME`` env var.

    Meaning: Platform cache base; ai-hats appends ``ai-hats/`` to it. Ranks below
    ``AI_HATS_CACHE_HOME`` and above ``user_home()`` when resolving the cache class.
    Documentation: ``docs/ARCHITECTURE.md`` (Materialization), HATS-1398.
    """
    return _read(ENV_XDG_CACHE_HOME)


__all__ = [
    "ENV_AI_HATS_USER_HOME",
    "ENV_AI_HATS_DIR",
    "AI_HATS_PROJECT_DIR_ENV",
    "ENV_AI_HATS_VENV",
    "ENV_LIBRARY_ROOT",
    "ENV_AI_HATS_CACHE_HOME",
    "ENV_XDG_CACHE_HOME",
    "ENV_SESSION_CACHE_DIR",
    "ENV_ROLE",
    "ENV_ROOT_PID",
    "ENV_AI_HATS_PYTHON",
    "ENV_HOOK_SOCKET",
    "ENV_HOOK_POINT",
    "ENV_IN_HOOK",
    "ENV_FORCE",
    "ENV_TASK_ID",
    "ENV_WORKTREE_PATH",
    "ENV_TASKS_DIR",
    "ENV_MERGED_SHA",
    "ENV_HOOK_CALL",
    "ENV_HOOK_TIMEOUT_S",
    "ENV_GATE_BROKEN_ACK",
    "ENV_GIT_HOOK_TIMEOUT_S",
    "ENV_GIT_GATE_BROKEN_ACK",
    "ENV_HOOK_EVENT",
    "ENV_HOOK_SURFACE_TIMEOUT_MS",
    "ENV_RETIRED_AGY_HOOK_TIMEOUT_S",
    "Budget",
    "read_budget",
    "OVERRIDES",
    "user_home_override",
    "ai_hats_dir_override",
    "project_dir_pin",
    "venv_override",
    "library_root_override",
    "tool_home_override",
    "cache_home_override",
    "xdg_cache_home",
]
