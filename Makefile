# Makefile for ai-hats

.PHONY: help tests unit e2e lint check

.DEFAULT_GOAL := help

# Timeout defaults calculated as ~2x observed execution times:
# - unit tests: ~148s observed -> 300s timeout
# - e2e suite:  ~450s observed -> 900s timeout
TIMEOUT_TESTS ?= 300
TIMEOUT_E2E   ?= 900
TIMEOUT_BIN   := $(shell command -v timeout 2>/dev/null || echo "$$HOME/.local/bin/timeout")

help: ## Display available targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

tests: unit ## Run unit/integration tests (bounded by 300s timeout)

unit: ## Run unit test suite bounded by timeout (default 300s)
	@if [ -x "$(TIMEOUT_BIN)" ]; then \
		$(TIMEOUT_BIN) $(TIMEOUT_TESTS) pytest tests/ --ignore=tests/e2e $(ARGS); \
	else \
		pytest tests/ --ignore=tests/e2e $(ARGS); \
	fi

e2e: ## Run e2e tests bounded by timeout (default 900s)
	@if [ -x "$(TIMEOUT_BIN)" ]; then \
		$(TIMEOUT_BIN) $(TIMEOUT_E2E) pytest tests/e2e/ -m integration $(ARGS); \
	else \
		pytest tests/e2e/ -m integration $(ARGS); \
	fi

lint: ## Run ruff linter and formatter check
	ruff check src/ tests/
	ruff format --check src/ tests/

check: lint unit ## Run lint and unit tests
