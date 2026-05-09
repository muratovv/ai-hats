"""Loader for user-authored pipeline steps (HATS-275).

Drop a Python file into ``<ai_hats_dir>/pipeline_steps/`` (default:
``<project>/.agent/ai-hats/pipeline_steps/``) that subclasses
``ai_hats.pipeline.step.Step`` and registers itself at module top
level via ``ai_hats.pipeline.registry.register(name, Cls)``.

``PipelineHarness.__enter__`` calls :func:`load_user_steps` before any
YAML pipeline is parsed, so user step IDs are resolvable from YAML
just like built-ins.

Conventions:
  - Files starting with ``_`` are skipped (e.g. ``_helpers.py`` for
    shared internals between user steps).
  - Module names are namespaced as ``_ai_hats_user_steps.<stem>`` to
    avoid colliding with anything else on ``sys.path``.
  - Loaded paths are cached for the process lifetime — re-entry of the
    harness does not re-import (and would otherwise hit
    ``StepRegistryError`` on repeated ``register()`` calls).

Errors propagate: a broken module surfaces ``ImportError`` /
``StepRegistryError`` / whatever it raised — failing fast at harness
startup, not deep inside ``pipeline.run``.

Security: code in this directory is executed by ai-hats, same threat
model as ``.agent/hooks/`` shell scripts. Do not put untrusted code
there.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ..paths import pipeline_steps_dir

_LOADED: set[str] = set()


def load_user_steps(project_dir: Path) -> list[Path]:
    """Import every ``*.py`` (sans ``_``-prefix) under
    ``pipeline_steps_dir(project_dir)``.

    Idempotent: a path imported once stays loaded. Returns the list of
    paths actually imported on this call (may be empty).
    """
    steps_dir = pipeline_steps_dir(project_dir)
    loaded: list[Path] = []
    for path in sorted(steps_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        key = str(path.resolve())
        if key in _LOADED:
            continue
        spec = importlib.util.spec_from_file_location(
            f"_ai_hats_user_steps.{path.stem}", path,
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"could not build spec for {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADED.add(key)
        loaded.append(path)
    return loaded


def _reset_loader_cache() -> None:
    """Test-only: forget all loaded paths.

    Pair with ``registry._reset_for_tests`` in test fixtures; otherwise
    a re-load would skip a module that the registry has already
    forgotten.
    """
    _LOADED.clear()
