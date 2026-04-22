"""Prompt templates for retro builder and judge runner."""

from __future__ import annotations

SUMMARY_PROMPT = """\
You are analyzing a session retrospective. Based on the session audit and metrics
below, produce:

1. A one-paragraph narrative summary (2-4 sentences) describing what the agent was
   asked to do, what it did, and whether it succeeded. Plain prose, no bullets.
2. Between 0 and 6 short factual observations (one line each, each under 140
   characters — split or shorten if you exceed) about concrete events in the
   session. No prescriptions, no opinions, no "could have done X" — just factual
   notes like "used Grep instead of Glob to find files" or "retried edit after
   Read-before-Edit error".

Output format (strict):
SUMMARY: <single paragraph>
OBSERVATIONS:
- <observation 1>
- <observation 2>
...

--- AUDIT ---
{audit_text}

--- METRICS ---
{metrics_json}
"""


def parse_summary_response(text: str) -> tuple[str, list[str]]:
    """Parse `SUMMARY: ...\\nOBSERVATIONS:\\n- ...\\n- ...` into (summary, observations).

    Robust to preamble/epilogue: searches for the SUMMARY: marker and processes
    everything from there. Observations are everything after `OBSERVATIONS:` that
    starts with `-` (dash bullet), trimmed of the dash.
    """
    if not text:
        return "", []

    summary_idx = text.find("SUMMARY:")
    if summary_idx == -1:
        # No marker — best-effort: treat first non-empty line as summary
        first_line = next(
            (line.strip() for line in text.splitlines() if line.strip()),
            "",
        )
        return first_line, []

    after = text[summary_idx + len("SUMMARY:"):]
    obs_idx = after.find("OBSERVATIONS:")
    if obs_idx == -1:
        return after.strip(), []
    summary = after[:obs_idx].strip()

    obs_section = after[obs_idx + len("OBSERVATIONS:"):]
    observations: list[str] = []
    for raw_line in obs_section.splitlines():
        line = raw_line.strip()
        if not line:
            if observations:
                # Stop at the first blank line after observations started
                # (allows trailing notes / multiple sections)
                break
            continue
        if line.startswith("- "):
            observations.append(line[2:].strip())
        elif line.startswith("-"):
            observations.append(line[1:].strip())
        else:
            # Non-bullet line ends the observations block
            if observations:
                break
    return summary, observations
