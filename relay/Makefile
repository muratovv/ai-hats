# Makefile for hats-relay

.PHONY: help run-server run-client test check-venv check-token

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

# The relay does not run unauthenticated, and server and client must agree on the same
# value — so the token comes from the environment rather than from a Make variable: a
# variable would land in argv, where `ps` shows it to everyone else on the box.
check-token:
	@if [ -z "$$HATS_RELAY_TOKEN" ]; then \
		echo "Error: HATS_RELAY_TOKEN is not set — the relay does not run unauthenticated." >&2; \
		echo "  export HATS_RELAY_TOKEN=\"\$$(openssl rand -hex 16)\"" >&2; \
		echo "Export it in this shell AND in the one running the other side." >&2; \
		exit 1; \
	fi

run-server: check-venv check-token ## Run local hats-relay server (options: HOST, PORT, ARGS)
	PYTHONPATH=src $(PYTHON) -m hats_relay --host $(HOST) --port $(PORT) $(ARGS)

run-client: check-venv check-token ## Run local hats-relay client (options: HOST, PORT, ROLE, ARGS)
	PYTHONPATH=src $(PYTHON) -m hats_relay.client ws://$(HOST):$(PORT) --role $(ROLE) $(ARGS)

test: check-venv ## Run relay test suite
	$(PYTHON) -m pytest tests $(ARGS)
