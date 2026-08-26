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

# comment-length: allow — the invariant IS why this line exists
# ai-hats' own session id, `YYYYMMDD-HHMMSS-<n>-<pid>`. Not opaque: its first 15
# characters ARE the start time, and `paths.session_start_ts` parses them to bound
# the transcript search — so `str` accepts values the readers cannot use.
# TODO(HATS-1849): a type that parses and refuses, instead of a slice and a strptime.
SessionId = str

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

# Reopens a finished session by id and directory, for the audit step to read.
# TODO(HATS-1785)
SessionFactory = object

# Builds the writer that rewrites a finished session's ``audit.md``.
# TODO(HATS-1785)
AuditWriterFactory = object

# Recomputes a session's cost from its transcript when the provider reports none.
# TODO(HATS-1785)
StaticCostAnalyzer = object
