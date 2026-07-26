# Makefile for hats-relay

.PHONY: help run-server run-client test check-venv

.DEFAULT_GOAL := help

HOST ?= 127.0.0.1
PORT ?= 8787
ROLE ?= assistant
VENV := .venv
PYTHON := $(VENV)/bin/python

help: ## Display available targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

check-venv:
	@if [ ! -x "$(PYTHON)" ]; then \
		echo "Error: relay/.venv missing or incomplete." >&2; \
		echo "Please run: cd relay && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2; \
		exit 1; \
	fi

run-server: check-venv ## Run local hats-relay server (options: HOST, PORT, ARGS)
	PYTHONPATH=src $(PYTHON) -m hats_relay --host $(HOST) --port $(PORT) $(ARGS)

run-client: check-venv ## Run local hats-relay client (options: HOST, PORT, ROLE, ARGS)
	PYTHONPATH=src $(PYTHON) -m hats_relay.client ws://$(HOST):$(PORT) --role $(ROLE) $(ARGS)

test: check-venv ## Run relay test suite
	$(PYTHON) -m pytest tests $(ARGS)
