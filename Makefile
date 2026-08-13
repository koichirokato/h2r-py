# h2r-py Makefile — all commands run inside the Docker dev container.
# Single source of truth for tooling invocations: CI, the pre-push hook, and
# .pre-commit-config.yaml all call these targets rather than `docker compose
# run` directly.

.PHONY: build shell sync ruff-check ruff-check-fix ruff-format-check fmt ty \
	lint test check pre-commit-install clean help

# Run the dev container as the host user (see docker-compose.yml's `user:`
# field) so files it writes into bind mounts / volumes stay host-owned.
export DEV_UID := $(shell id -u)
export DEV_GID := $(shell id -g)

## Build the dev Docker image
build:
	docker compose build

## Install the pre-commit git hook (runs on the host via `uvx`; requires `uv`)
pre-commit-install:
	uvx pre-commit install

## Open an interactive shell in the dev container
shell:
	docker compose run --rm dev bash

## Install / sync Python dependencies (persisted in the .venv named volume)
sync:
	docker compose run --rm dev uv sync --extra dev

## ruff check, no fixes (CI / pre-push)
ruff-check:
	docker compose run --rm dev uv run ruff check .

## ruff check --fix, writes changes (pre-commit)
ruff-check-fix:
	docker compose run --rm dev uv run ruff check --fix .

## ruff format --check, no writes (CI / pre-push)
ruff-format-check:
	docker compose run --rm dev uv run ruff format --check .

## ruff format, writes changes (pre-commit / manual use)
fmt:
	docker compose run --rm dev uv run ruff format .

## ty check at max strictness
ty:
	docker compose run --rm dev uv run ty check --error all src/h2r tests

## Lint: ruff check, ruff format --check, ty (no writes)
lint: ruff-check ruff-format-check ty

## Run the test suite
test:
	docker compose run --rm dev uv run pytest -v

## Full check: lint + test (same as CI and the pre-push hook)
check: lint test

## Remove containers, volumes, and local caches
clean:
	docker compose down -v
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

## Show available targets
help:
	@grep -E '^## ' Makefile | sed 's/## /  /'
