"""YAML pipeline loader — instantiates steps via the open registry.

YAML schema:

    name: <str>
    steps:
      - id: <step_name>          # FQN from registry
        params: {<key>: <val>}   # optional, step-specific
        harness:                 # optional, HATS-378 reliability policy
          reporting: <bool>
          on_zero_output: harness_incident | ignore
          on_timeout:
            retry: <int>
            budget_multiplier: <float>
            then: harness_incident

The loader resolves each ``id`` against ``pipeline.registry``, builds the
Step with its declared ``params``, attaches the optional ``harness``
policy, and assembles a ``Pipeline``. Build-time consistency (every
step's ``requires`` is producible) is then re-checked at ``pipeline.run``
against the actual initial state.

Run as a module for dry-run inspection:

    python -m ai_hats.pipeline.loader path/to/pipeline.yaml
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import registry, steps  # noqa: F401  — import registers built-ins
from .harness_policy import HarnessPolicyError, parse_harness_policy
from .pipeline import Pipeline, build


class PipelineYamlError(ValueError):
    """Malformed pipeline YAML or unresolvable step reference."""


# HATS-566: memoize core pipelines to guard against editable-install
# YAML drift. When the ai-hats source tree is updated mid-session
# (``git pull`` / merge during a long-running ``WrapRunner`` PTY
# session), ``paths.core_pipeline_path`` resolves to the live working
# tree (cwd/worktree-aware, HATS-831) — so a fresh ``load_core_pipeline`` call at
# session-end reads the *new* YAML against the *old* step registry
# that was imported at process start. Memoizing the parsed Pipeline at
# first access (combined with eager preload from ``WrapRunner.run``
# before PTY spawn) freezes the YAML against the matching registry
# snapshot. Wheel/site-packages installs are immutable so unaffected;
# the cache is harmless there.
_CORE_PIPELINE_CACHE: dict[str, Pipeline] = {}


def load_core_pipeline(name: str, *, use_cache: bool = True) -> Pipeline:
    """Load a pipeline by name from ``ai_hats.library/core/pipelines/``.

    Convenience wrapper around :func:`load_pipeline` for the common case
    of loading built-in pipelines (``human``, ``execute``,
    ``finalize-hitl``, ``finalize-subagent``, …). Used by runtime
    finalization to invoke sub-pipelines without going through the
    :class:`PipelineHarness` (which creates a per-session namespace dir
    + retention sweep — overkill for an inline sub-pipeline).

    Memoized by default (HATS-566); pass ``use_cache=False`` for tests
    that rebuild the registry between calls.
    """
    if use_cache and name in _CORE_PIPELINE_CACHE:
        return _CORE_PIPELINE_CACHE[name]

    from ..paths import core_pipeline_path

    yaml_path = core_pipeline_path(name)
    if yaml_path is None:
        raise PipelineYamlError(
            f"core pipeline {name!r} not found — ai_hats.library missing (broken install)"
        )
    pipeline = load_pipeline(yaml_path)
    if use_cache:
        _CORE_PIPELINE_CACHE[name] = pipeline
    return pipeline


def clear_core_pipeline_cache() -> None:
    """Drop the memoized core-pipeline cache (HATS-566).

    Intended for tests that swap the step registry or monkey-patch
    pipeline YAMLs between cases; production code never needs this.
    """
    _CORE_PIPELINE_CACHE.clear()


def load_pipeline(yaml_path: Path) -> Pipeline:
    raw = Path(yaml_path).read_text()
    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise PipelineYamlError(f"{yaml_path}: invalid YAML: {e}") from e

    if not isinstance(data, dict):
        raise PipelineYamlError(
            f"{yaml_path}: top-level must be a mapping, got {type(data).__name__}"
        )

    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise PipelineYamlError(f"{yaml_path}: 'name' must be a non-empty string")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PipelineYamlError(f"{yaml_path}: 'steps' must be a non-empty list")

    instances = []
    for i, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            raise PipelineYamlError(
                f"{yaml_path}: steps[{i}] must be a mapping"
            )
        step_id = item.get("id")
        if not isinstance(step_id, str) or not step_id:
            raise PipelineYamlError(
                f"{yaml_path}: steps[{i}].id must be a non-empty string"
            )
        params = item.get("params") or {}
        if not isinstance(params, dict):
            raise PipelineYamlError(
                f"{yaml_path}: steps[{i}].params must be a mapping (got "
                f"{type(params).__name__})"
            )
        try:
            factory = registry.get(step_id)
        except registry.StepRegistryError as e:
            raise PipelineYamlError(
                f"{yaml_path}: steps[{i}] {e}"
            ) from e
        try:
            instance = factory(params)
        except (TypeError, ValueError) as e:
            raise PipelineYamlError(
                f"{yaml_path}: steps[{i}] ({step_id}): {e}"
            ) from e
        # HATS-378: optional harness reliability policy. Additive —
        # steps without `harness:` keep the base-class default (None).
        harness_raw = item.get("harness")
        if harness_raw is not None:
            try:
                instance.harness_policy = parse_harness_policy(harness_raw)
            except HarnessPolicyError as e:
                raise PipelineYamlError(
                    f"{yaml_path}: steps[{i}] ({step_id}): harness: {e}"
                ) from e
        instances.append(instance)

    return build(*instances, name=name)


def main(argv: list[str] | None = None) -> int:
    """Dry-run inspector — validate YAML and print the resolved IO graph."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m ai_hats.pipeline.loader",
        description="Inspect a pipeline YAML — validate registry resolution "
                    "and print the IO graph. No execution.",
    )
    parser.add_argument("yaml_path", type=Path, help="Path to pipeline YAML")
    args = parser.parse_args(argv)

    p = load_pipeline(args.yaml_path)
    print(f"Pipeline: {p.io.name}")
    print(f"  external requires: {sorted(p.io.requires)}")
    print(f"  external optional: {sorted(p.io.optional)}")
    print(f"  produces:          {sorted(p.io.produces)}")
    print()
    print("Steps:")
    for i, s in enumerate(p.steps, 1):
        io = s.io
        print(
            f"  {i}. {io.name:<22} "
            f"requires={sorted(io.requires)} "
            f"optional={sorted(io.optional)} "
            f"produces={sorted(io.produces)}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
