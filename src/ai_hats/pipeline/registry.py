"""Open registry for pipeline steps — the YAML ``id`` field resolves here.

Built-in steps are **declared**, not registered: ``pyproject.toml`` advertises
each id under the ``ai_hats.steps`` group, and :func:`get` imports the module
named there the first time that id is used. Nothing imports a step to make it
resolvable (HATS-1783) — that side effect is what put the loader in an import
cycle with ``cli``. Third parties keep both doors: advertise the same group, or
call :func:`register` at their own import time, as ``user_steps`` does.

One id, one owner, through either door: :func:`register` refuses a name that is
already registered **or** advertised, so a project step cannot take a built-in's
id — whether or not anything has resolved that built-in yet (HATS-1799).

ADR-0026 D15: an entry point is a **non-import** edge no AST walk can see, so
this seam is outside import-based test selection by design — a change to the
group runs the full suite, never a selected subset.
"""  # comment-length: allow — the seam's contract and why it is not an import

from __future__ import annotations

import importlib.metadata
from typing import Any, Callable, Mapping

from .step import Step


StepFactory = Callable[[Mapping[str, Any]], Step]

#: The IoC seam. ai-hats declares its own built-ins here; so may anyone else.
STEP_ENTRY_POINT_GROUP = "ai_hats.steps"

_REGISTRY: dict[str, StepFactory] = {}

#: name -> every EntryPoint claiming it, read once per process. ``None`` = unscanned.
#: A list, not one entry point: two distributions claiming one id is a collision
#: :func:`register` would refuse, and it must not become "whichever came last".
_ADVERTISED: dict[str, list[Any]] | None = None


class StepRegistryError(KeyError):
    """Raised when a step name is unknown or already registered."""


def register(name: str, factory: StepFactory) -> None:
    """Claim ``name``. A taken id is refused — never silently overridden.

    Taken means either half of what :func:`names` reports: already registered,
    or advertised and not yet resolved. Checking only ``_REGISTRY`` would make
    the refusal depend on whether something had happened to resolve the built-in
    first, so in a fresh process a project step would shadow it (HATS-1799).
    """
    if name in _REGISTRY:
        raise StepRegistryError(f"step already registered: {name!r}")
    claims = _advertised().get(name)
    if claims:
        raise StepRegistryError(
            f"step already registered: {name!r} is a built-in, advertised under "
            f"{STEP_ENTRY_POINT_GROUP!r} as {sorted(ep.value for ep in claims)}. "
            "Overriding a built-in is not supported — pick a different id."
        )
    _REGISTRY[name] = factory


def _advertised() -> dict[str, list[Any]]:
    """Every step id an installed distribution advertises, memoized.

    Metadata is read, never imported: the values are ``EntryPoint``s, and the
    module behind one stays unimported until :func:`get` asks for that id.
    """
    global _ADVERTISED
    if _ADVERTISED is None:
        found: dict[str, list[Any]] = {}
        for ep in importlib.metadata.entry_points(group=STEP_ENTRY_POINT_GROUP):
            found.setdefault(ep.name, []).append(ep)
        _ADVERTISED = found
    return _ADVERTISED


def _no_metadata_error(name: str) -> StepRegistryError:
    """The uninstalled / stale-install case, refused loudly and by name.

    Entry points reach a process through **installed** metadata, so the built-in
    steps are unresolvable in a bare source tree (``PYTHONPATH=src`` with nothing
    installed) and in an install whose ``entry_points.txt`` predates HATS-1783.
    Refusing is the decision, not an accident: the alternative — a module-path
    table in this file as a fallback — would be a second owner of the same
    mapping (ADR-0026 D3), and it would keep every pipeline running while the
    declarations were missing from the built distribution, which is the one
    failure ``tests/e2e/test_step_entry_point_resolution.py`` exists to catch.
    """  # comment-length: allow — the decision has to be readable where it fires
    return StepRegistryError(
        f"cannot resolve step {name!r}: no installed distribution advertises the "
        f"{STEP_ENTRY_POINT_GROUP!r} entry-point group, so no built-in step exists "
        "for this process. The built-ins are declared in ai-hats' pyproject.toml and "
        "reach the process through installed metadata — a bare source tree, or an "
        "install whose metadata predates HATS-1783, advertises none. Reinstall the "
        "package (`uv sync`, or `pip install -e .`) and retry."
    )


def get(name: str) -> StepFactory:
    """The factory for ``name``, importing its module on first use."""
    factory = _REGISTRY.get(name)
    if factory is not None:
        return factory

    advertised = _advertised()
    if not advertised:
        raise _no_metadata_error(name)

    claims = advertised.get(name)
    if not claims:
        raise StepRegistryError(
            f"unknown step: {name!r}. Registered: {names()} "
            f"(built-ins are advertised under {STEP_ENTRY_POINT_GROUP!r}; an id missing "
            "from a package you did install means that package's metadata is stale)"
        )
    if len(claims) > 1:
        raise StepRegistryError(
            f"step {name!r} is advertised by more than one distribution: "
            f"{sorted(ep.value for ep in claims)}. One id, one owner — "
            f"{STEP_ENTRY_POINT_GROUP!r} is open, but silently taking one of them is not "
            "an answer (register() refuses the same collision)."
        )
    entry_point = claims[0]

    try:
        factory = entry_point.load()
    except Exception as exc:
        raise StepRegistryError(
            f"step {name!r} is advertised as {entry_point.value!r} under "
            f"{STEP_ENTRY_POINT_GROUP!r} but will not load: {exc.__class__.__name__}: {exc}"
        ) from exc
    # Not register(): resolving a declaration is not claiming the name, and the
    # id is advertised by definition here — which is what register() refuses.
    _REGISTRY[name] = factory
    return factory


def names() -> list[str]:
    """Every id that resolves — registered plus advertised-but-not-yet-imported.

    Advertised ids belong here because they resolve: a caller asking "is this
    step wired?" gets the same answer before and after the module is imported,
    and the unknown-id error lists what a pipeline may actually name.
    """
    return sorted(set(_REGISTRY) | set(_advertised()))


def _reset_for_tests() -> None:
    """Forget what this process resolved, and the memoized metadata scan.

    Not "empty": built-ins are declarations, not registrations, so they come
    back from metadata the moment anything asks — :func:`names` and
    :func:`register`'s refusal read the same either side of a reset. What a
    reset does drop is every resolved factory and every ``register`` call, plus
    the memoized scan, so a monkeypatched ``entry_points`` takes effect after it.
    """
    global _ADVERTISED
    _REGISTRY.clear()
    _ADVERTISED = None
