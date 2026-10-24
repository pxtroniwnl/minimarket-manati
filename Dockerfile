FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias primero (capa que se reutiliza mientras no cambie uv.lock).
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN uv sync --locked --no-dev && useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000
# Un solo worker: el candado por cliente y el tiempo real (SSE) viven en memoria [H10].
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
