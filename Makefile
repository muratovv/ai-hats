# Makefile for ai-hats

.PHONY: help tests e2e lint check

.DEFAULT_GOAL := help

TIMEOUT ?= 300
TIMEOUT_BIN := $(shell command -v timeout 2>/dev/null || echo "$$HOME/.local/bin/timeout")

help: ## Display available targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

tests: ## Run pytest suite bounded by timeout (default 300s)
	@if [ -x "$(TIMEOUT_BIN)" ]; then \
		$(TIMEOUT_BIN) $(TIMEOUT) pytest $(ARGS); \
	else \
		pytest $(ARGS); \
	fi

e2e: ## Run e2e tests bounded by timeout (default 300s)
	@if [ -x "$(TIMEOUT_BIN)" ]; then \
		$(TIMEOUT_BIN) $(TIMEOUT) pytest tests/e2e/ -m integration $(ARGS); \
	else \
		pytest tests/e2e/ -m integration $(ARGS); \
	fi

lint: ## Run ruff linter and formatter check
	ruff check src/ tests/
	ruff format --check src/ tests/

check: lint tests ## Run lint and tests
