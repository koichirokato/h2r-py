FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Matches the host UID/GID docker-compose.yml runs the container as (its
# `user:` field). The build stage runs as root regardless, so the .venv it
# bakes in here — the seed content Docker copies into the runtime named
# volume on first use — needs an explicit chown to be writable by that user.
ARG DEV_UID=1000
ARG DEV_GID=1000

WORKDIR /workspace

# Volume-mounted cache/venv may live on a different filesystem than the
# image layers, where hardlinking silently falls back to slow full copies.
ENV UV_LINK_MODE=copy

# Cache dependency resolution separately from source code.
COPY pyproject.toml ./
RUN uv sync --extra dev --no-install-project

COPY . .
RUN uv sync --extra dev

RUN chown -R ${DEV_UID}:${DEV_GID} /workspace/.venv

CMD ["bash"]
