"""No launch step reads the ``provider`` funnel key (HATS-1218).

The CLI used to seed ``provider`` into the ``human`` / ``execute`` funnels next
to the ``CompositionPayload``, which already carries the resolved provider. No
step ever declared the key, so the seed was inert — but two parallel records of
one decision is how they drift apart. The seeds are gone; this guards the
premise that let them go, and fails the day a step starts wanting the key
(add it back deliberately, derived from the payload — never hand-seeded again).
"""

from __future__ import annotations

import pytest

from ai_hats.pipeline.keys import KEY_PROVIDER
from ai_hats.pipeline.loader import load_core_pipeline


@pytest.mark.parametrize("pipeline_name", ["human", "execute"])
def test_no_launch_step_declares_the_provider_key(pipeline_name: str):
    pipeline = load_core_pipeline(pipeline_name)
    readers = [
        step.io.name
        for step in pipeline.steps
        if KEY_PROVIDER in (step.io.requires | step.io.optional)
    ]
    assert readers == [], (
        f"{pipeline_name}: {readers} now read {KEY_PROVIDER!r}, but the CLI no "
        f"longer seeds it — derive it from the composition payload instead."
    )
