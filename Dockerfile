# --- build: resolve dependencies with uv into /app/.venv -----------------------
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

# --- runtime: same Python as the build image, without uv or the sources ---------
FROM python:3.11-slim-bookworm

RUN useradd --system --uid 10001 --no-create-home netmcp

COPY --from=build /app/.venv /app/.venv

# HTTP by default (http://<host>:8088/mcp); run with -e MCP_PORT= for stdio.
# grpc_proxy="" keeps gNMI off any http(s)_proxy inherited from the host.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8088 \
    grpc_proxy=""

# Inventory is mounted here: /inventory/netmcp.yml (or /inventory/containerlab/*.clab.yml).
WORKDIR /inventory

USER netmcp
EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, socket; socket.create_connection(('127.0.0.1', int(os.environ.get('MCP_PORT') or 8088)), 3)"]

CMD ["netmcp"]
