FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy everything needed for the build
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install dependencies and application
RUN uv sync --frozen --no-dev

# Default to running the server
CMD ["uv", "run", "kairix-server"]
