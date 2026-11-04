FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Първо само зависимостите, за да се кешират между билдовете
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY *.py ./

ENV PATH="/app/.venv/bin:$PATH" \
    IMOT_DATA_DIR=/data

EXPOSE 7777

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7777"]
