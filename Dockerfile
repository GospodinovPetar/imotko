FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim@sha256:7cf77f594be8042dab6daa9fe326f90962252268b4f120a7f5dccce4d947e6c1 AS build

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project


FROM python:3.14-slim-bookworm@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527

WORKDIR /app

RUN useradd --create-home --uid 1000 imotko

COPY --from=build /app/.venv ./.venv
COPY app.py chat.py watch.py ./
COPY imotko/ ./imotko/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    IMOT_DATA_DIR=/data

USER imotko

EXPOSE 7777

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7777"]
