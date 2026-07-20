"""One home for the rack's YAML read loader (HATS-1065).

The pure-Python ``SafeLoader`` dominated ``rack ls`` latency — parsing ~600
``task.yaml`` cards cost ~800 ms vs ~50 ms with libyaml. ``CSafeLoader`` is a
drop-in for ``safe_load`` on trusted files and constructs identical Python
objects (same ``SafeConstructor``; only the scanner/parser is C). When libyaml
is not built we fall back to the pure loader — correct, just slower.
"""

from __future__ import annotations

from typing import Any

import yaml

try:
    from yaml import CSafeLoader as _SafeLoader
except ImportError:  # pragma: no cover — libyaml not built in this environment
    from yaml import SafeLoader as _SafeLoader


def load(text: str) -> Any:
    """``yaml.safe_load`` over trusted rack files, on libyaml when available."""
    return yaml.load(text, Loader=_SafeLoader)


__all__ = ["load"]
