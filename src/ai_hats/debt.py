"""Types the code carries but has not decided yet — declared once, in one place.

Two failures this file exists to prevent, both seen in review (HATS-1783): a module
declaring its own ``TracerFactory = object`` because it never read the neighbour that
already had one, and the next agent guessing whether that neighbour's ``Tracer`` and
this one's ``TracerFactory`` are the same value.

Rules for this file:

- a name here is a promise, so it carries the card that will retire it;
- if a value already has a name here, use it — never re-alias it locally;
- the card retires the entry by giving the value a real type, and deletes the line.
"""

from __future__ import annotations

# The composed role payload: built by composition, read by the launch step, carried
# by the pipeline area without being looked into.
# TODO(HATS-1785): real type once composition owns a public contract.
CompositionPayload = object

# Creates a session's directory and its metrics file (ai_hats_observe today).
# TODO(HATS-1785)
SessionManager = object

# Builds the sidecar tracer that captures a session's transcript.
# TODO(HATS-1785)
TracerFactory = object
