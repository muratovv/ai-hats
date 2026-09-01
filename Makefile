# Makefile for ai-hats
#
# Every target delegates to scripts/ci-local.sh, the single source of the check
# commands CI runs (HATS-922/725). Spelling a command out here instead forks the
# definition of the gate — tests/test_gate_entrypoint_parity.py refuses that.

.PHONY: help tests unit integration e2e lint check gates coverage security version-skew dependency-floor python-pin silent-fallback test-isolation review-gate merge-gate done-gate relay-server relay-client

.DEFAULT_GOAL := help

# Timeout defaults calculated as ~2x observed execution times:
# - unit stage: ~90s observed -> 300s timeout
# - e2e stage:  ~1700s observed -> 3600s timeout
TIMEOUT_TESTS ?= 300
TIMEOUT_E2E   ?= 3600
TIMEOUT_BIN   := $(shell command -v timeout 2>/dev/null || echo "$$HOME/.local/bin/timeout")
PYTHON        ?= python
CI_LOCAL      := env PYTHON=$(PYTHON) bash scripts/ci-local.sh

# $(1) = ci-local stage, $(2) = timeout budget in seconds
define timed_stage
@if [ -x "$(TIMEOUT_BIN)" ]; then \
	$(TIMEOUT_BIN) $(2) $(CI_LOCAL) $(1) $(ARGS); \
	status=$$?; \
	if [ $$status -eq 124 ]; then \
		printf "\n%s stage exceeded %ss timeout — raise the budget or investigate a hang\n" "$(1)" "$(2)" >&2; \
		exit 124; \
	else \
		exit $$status; \
	fi; \
else \
	$(CI_LOCAL) $(1) $(ARGS); \
fi
endef

help: ## Display available targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

tests: unit ## Alias for unit

unit: ## Run the unit stage (bounded by TIMEOUT_TESTS, default 300s)
	$(call timed_stage,unit,$(TIMEOUT_TESTS))

integration: ## Run the integration stage (real-subprocess tests outside tests/e2e)
	$(call timed_stage,integration,$(TIMEOUT_TESTS))

lint: ## Run the lint stage (ruff check + formatter check)
	$(CI_LOCAL) lint

check: lint unit ## Fast inner loop: lint + unit only — full parity gate is `make gates`

gates: ## Run every stage CI runs locally (the `all` bundle in scripts/ci-local.sh)
	$(CI_LOCAL) all

coverage: ## Run the coverage stage (unit + non-e2e integration, --cov-fail-under=78)
	$(CI_LOCAL) coverage $(ARGS)

security: ## Run the security stage (pip-audit; env-scoped, CI is authoritative)
	$(CI_LOCAL) security $(ARGS)

version-skew: ## Check workspace packages are ahead of PyPI (needs network)
	$(CI_LOCAL) version-skew $(ARGS)

dependency-floor: ## Check every pin on a workspace package tracks its version
	$(CI_LOCAL) dependency-floor

python-pin: ## Check every copy of the Python pin agrees, and CI runs it
	$(CI_LOCAL) python-pin

silent-fallback: ## Check no broad except swallows a failure without reporting it
	$(CI_LOCAL) silent-fallback

test-isolation: ## Check the suite patches its units no more than the baseline
	$(CI_LOCAL) test-isolation

e2e: ## Run the e2e stage — the same selection the master pre-push gate runs
	$(call timed_stage,e2e,$(TIMEOUT_E2E))

# The gates the road to master demands. Run one in the TASK WORKTREE: the
# marker is keyed to the tree you run it on, which is the content the check looks
# up. `ci-local.sh --stages <gate>` names what each runs.
#
# `REV=<sha>` judges ONE COMMIT instead of this checkout, in a scratch worktree
# of its own — what a card whose worktree is already merged away needs, and what
# the gate's own refusal hands you when that is the case (HATS-1664).
#
# $(1) = gate name, which is also its script's basename
define run_gate
@py="$(PYTHON)"; [ -x "$(CURDIR)/.venv/bin/python3" ] && py="$(CURDIR)/.venv/bin/python3"; \
libroot="$$("$$py" -c 'import ai_hats_library, pathlib; print(pathlib.Path(ai_hats_library.__file__).parent)' 2>/dev/null || true)"; \
hook="$$libroot/ai-hats-dev/skills/maintainer-quality-gate/hooks/$(1).sh"; \
if [ -z "$$libroot" ] || [ ! -f "$$hook" ]; then \
	printf "cannot resolve the $(1) in the ai-hats library — install it here first: ai-hats self init\n" >&2; \
	exit 1; \
fi; \
env PYTHON="$$py" bash "$$hook" --run $(if $(REV),--rev $(REV),)
endef

review-gate: ## Run the ->review gate here (or on REV=<sha>) and mark that tree green (HATS-1877)
	$(call run_gate,review-gate)

merge-gate: ## Run the ->merge gate here (or on REV=<sha>) and mark that tree green (HATS-1614)
	$(call run_gate,merge-gate)

done-gate: ## Run the ->done gate here (or on REV=<sha>) and mark that tree green (HATS-1137)
	$(call run_gate,done-gate)

relay-server: ## Run local hats-relay server (delegates to relay/Makefile)
	$(MAKE) -C relay run-server ARGS="$(ARGS)"

relay-client: ## Run local hats-relay client (delegates to relay/Makefile)
	$(MAKE) -C relay run-client ARGS="$(ARGS)"
