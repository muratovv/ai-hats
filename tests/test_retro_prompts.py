"""Form-level regression tests for retro prompt templates.

These tests check that SUMMARY_PROMPT contains the structural pieces the rest
of the pipeline depends on (format markers, placeholders, constraint hints).
Behavioural validation (running the prompt against a real LLM) is out of scope
for unit tests — see the eval harness task (HATS-153).
"""

from __future__ import annotations

from ai_hats.retro.prompts import SUMMARY_PROMPT


def test_summary_prompt_includes_observation_length_bound():
    """PoC HATS-151 showed long sessions produce observations exceeding 140 chars.

    The prompt must explicitly instruct the model to keep each observation
    under 140 characters. Regression guard for HATS-152.
    """
    # Normalize whitespace so line-wrapping in the template does not break the match.
    normalized = " ".join(SUMMARY_PROMPT.split())
    assert "140 character" in normalized, (
        "SUMMARY_PROMPT must instruct the model to keep observations "
        "under 140 characters (see HATS-152 / HATS-151 PoC)."
    )


def test_summary_prompt_structure_preserved():
    """Structural markers the parser and builder depend on must stay intact."""
    required_markers = (
        "SUMMARY:",
        "OBSERVATIONS:",
        "--- AUDIT ---",
        "--- METRICS ---",
        "{audit_text}",
        "{metrics_json}",
    )
    missing = [m for m in required_markers if m not in SUMMARY_PROMPT]
    assert not missing, f"SUMMARY_PROMPT missing required markers: {missing}"


def test_summary_prompt_observations_bounded_by_count():
    """The '0..6 observations' constraint remains after length-bound addition."""
    assert "Between 0 and 6" in SUMMARY_PROMPT, (
        "SUMMARY_PROMPT must retain the 'Between 0 and 6' observations limit."
    )
