FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /workspace

# Volume-mounted cache/venv may live on a different filesystem than the
# image layers, where hardlinking silently falls back to slow full copies.
ENV UV_LINK_MODE=copy

# Cache dependency resolution separately from source code.
COPY pyproject.toml ./
RUN uv sync --extra dev --no-install-project

COPY . .
RUN uv sync --extra dev

CMD ["bash"]
