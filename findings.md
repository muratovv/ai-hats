# Findings for HATS-1556

## Round 1 Findings

1. `tests/e2e/test_env_scrub.py`: Sits in `tests/e2e/` but has no `pytestmark = pytest.mark.integration` and is an unmarked pure-function unit test with no subprocess/integration fixture. Candidate for relocation to `tests/unit/` under HATS-1499.
2. `tests/e2e/test_env_scrub.py::test_build_src_clone_is_bounded`: Tests clone timeouts (HATS-1247), which is unrelated to environment scrubbing. Candidate for relocation or splitting under HATS-1499.
