"""e2e framework helpers (HATS-474 Phase 4).

Thin layer over production primitives — see ``live.py`` for the
multi-turn agent driver and ``project.py`` for subprocess fixtures.
The original PTY-based ``LiveSession`` lived here too; that approach
was retired after HATS-473 (idle-detection broke against the claude
TUI's continuous ANSI stream) and the new ``live_session`` wraps
:meth:`ai_hats.runtime.SubAgentRunner.session` instead.
"""
