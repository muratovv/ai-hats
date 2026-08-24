"""Built-in steps for ai-hats pipelines — one module per step, imported on demand.

Importing this package registers nothing and pulls in no step (HATS-1783). Each
built-in is declared under the ``ai_hats.steps`` entry-point group in
``pyproject.toml``, and ``pipeline.registry`` imports the module named there the
first time a YAML names that id. The id itself is still spelled once, in the
step's own ``StepIO`` (HATS-917); the entry-point key must equal it, and
``tests/test_literal_homes_contract.py`` holds the two together.

Registering built-ins at this package's import was the edge that made the YAML
loader depend on all twenty step modules — and through ``handoff`` on ``cli`` —
which is the cycle ADR-0026 D12 requires cut. Re-adding it is caught by
``tests/test_area_boundary.py::test_no_shipped_module_registers_a_step_by_being_imported``.
"""  # comment-length: allow — the package's whole content is now this explanation

from __future__ import annotations
