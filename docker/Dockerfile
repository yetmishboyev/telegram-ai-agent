FROM python:3.13-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# uv o'rnatish
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Dependencies
COPY pyproject.toml ./
RUN UV_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu uv sync --no-dev --no-install-project

# Source code
COPY . .

# Papkalar
RUN mkdir -p logs sessions

# Non-root user
RUN adduser --disabled-password --gecos "" agentuser && chown -R agentuser:agentuser /app
USER agentuser

EXPOSE 8000

CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
