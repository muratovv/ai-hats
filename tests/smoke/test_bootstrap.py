"""Bootstrap smoke test — ensures `pytest -m smoke` has at least one collectible test.

Real smoke tests for individual integrations should be added alongside
the feature that uses them (e.g. tests/smoke/test_subagent_smoke.py).
This trivial test exists only so the smoke gate does not fall through to
exit-code 5 ("no tests collected") when no real smoke tests exist yet.
"""

import pytest


@pytest.mark.smoke
def test_smoke_bootstrap():
    """Canary test — if this passes, the smoke infrastructure works."""
    assert True
