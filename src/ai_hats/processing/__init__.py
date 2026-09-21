"""Processing — the rules by which the flat DSL becomes a plan (ADR-0035).

Pure functions over values, one module per stage; the one impure input is
``roots.read_roots``. The output is the plan's composition half (ADR-0036 D1).
"""

from .errors import ProcessingError

__all__ = ["ProcessingError"]
