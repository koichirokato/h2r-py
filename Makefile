# h2r-py Makefile — all commands run inside the Docker dev container.
# Single source of truth for tooling invocations: CI, the pre-push hook, and
# .pre-commit-config.yaml all call these targets rather than `docker compose
# run` directly.

.PHONY: build shell sync ruff-check ruff-check-fix ruff-format-check fmt ty \
	lint test check pre-commit-install clean help \
	bench-build bench-ros2-build bench-shell bench-ros2-shell \
	bench-h2r bench-zmq bench-grpc bench-ros2 bench-zenoh bench-tcp bench-udp \
	bench-websocket bench-mqtt bench-report bench-all

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
	docker compose run --rm dev uv run ty check --error all src/h2r tests examples

## Lint: ruff check, ruff format --check, ty (no writes)
lint: ruff-check ruff-format-check ty

## Run the test suite
test:
	docker compose run --rm dev uv run pytest -v --cov=src/h2r --cov-report=xml --cov-report=term-missing

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

# --- benchmarks/ (manual only: never called by `make check`, `make build`, or CI) ---

## Build the bench Docker image (h2r + ZeroMQ + gRPC + Zenoh)
bench-build:
	docker compose build bench

## Build the bench-ros2 Docker image (ROS 2 Humble)
bench-ros2-build:
	docker compose build bench-ros2

## Open an interactive shell in the bench container
bench-shell:
	docker compose run --rm bench bash

## Open an interactive shell in the bench-ros2 container
bench-ros2-shell:
	docker compose run --rm bench-ros2 bash

## Run the h2r benchmark across the default scenarios
bench-h2r:
	docker compose run --rm bench uv run python -m benchmarks.run_bench --middleware h2r

## Run the ZeroMQ benchmark across the default scenarios
bench-zmq:
	docker compose run --rm bench uv run python -m benchmarks.run_bench --middleware zmq

## Run the gRPC benchmark across the default scenarios
bench-grpc:
	docker compose run --rm bench uv run python -m benchmarks.run_bench --middleware grpc

## Run the ROS 2 benchmark across the default scenarios (BEST_EFFORT, 64-deep queue)
bench-ros2:
	docker compose run --rm bench-ros2 python3 -m benchmarks.run_bench --middleware ros2

## Run the Zenoh benchmark across the default scenarios
bench-zenoh:
	docker compose run --rm bench uv run python -m benchmarks.run_bench --middleware zenoh

## Run the raw TCP benchmark across the default scenarios (no pub/sub library, the floor)
bench-tcp:
	docker compose run --rm bench uv run python -m benchmarks.run_bench --middleware tcp

## Run the raw UDP benchmark across the default scenarios (no pub/sub library, the floor)
bench-udp:
	docker compose run --rm bench uv run python -m benchmarks.run_bench --middleware udp

## Run the WebSocket benchmark across the default scenarios
bench-websocket:
	docker compose run --rm bench uv run python -m benchmarks.run_bench --middleware websocket

## Run the MQTT benchmark across the default scenarios (paho-mqtt client + in-process amqtt broker)
bench-mqtt:
	docker compose run --rm bench uv run python -m benchmarks.run_bench --middleware mqtt

## Render benchmarks/results/*.json into a Markdown comparison report
bench-report:
	docker compose run --rm dev uv run python -m benchmarks.compare

## Run every middleware's benchmark, then render the comparison report
bench-all: bench-h2r bench-zmq bench-grpc bench-ros2 bench-zenoh bench-tcp bench-udp \
	bench-websocket bench-mqtt bench-report
