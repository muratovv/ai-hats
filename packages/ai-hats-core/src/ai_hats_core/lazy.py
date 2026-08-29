"""The ``__getattr__``/``__dir__`` pair a package needs to bind exports lazily.

Written six times by hand first (HATS-1869), which produced two conventions for
the same table and a ``__dir__`` missing from four of them. Imports stdlib only:
every caller is a package ``__init__`` on a hot import path, which is the whole
point of binding lazily.
"""

from __future__ import annotations

from importlib import import_module
from typing import Callable, Mapping


def lazy_facade(
    namespace: dict[str, object],
    homes: Mapping[str, str],
    *,
    aliases: Mapping[str, str] | None = None,
) -> tuple[Callable[[str], object], Callable[[], list[str]]]:
    """Bind ``homes`` on first use (PEP 562), for ``__getattr__, __dir__ = …``.

    ``namespace`` is the caller's ``globals()`` — it names the package and holds
    the bindings. ``homes`` maps an exported name to the **relative** module that
    defines it (``".contract"``); ``aliases`` maps an exported name to the name to
    fetch there, for a deprecated spelling of something already exported.
    """
    package = namespace["__name__"]
    aliases = aliases or {}

    def __getattr__(name: str) -> object:
        home = homes.get(name)
        if home is None:
            # Not ours. Raising lets `from pkg import x` fall back to importing
            # `pkg.x` as a submodule, which is how a submodule stays reachable.
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(import_module(home, package), aliases.get(name, name))
        namespace[name] = value  # bound once; later lookups skip __getattr__
        return value

    def __dir__() -> list[str]:
        return sorted({*namespace, *homes})

    return __getattr__, __dir__
