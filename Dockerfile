# syntax=docker/dockerfile:1
# Single image, SQLite on a volume by default; set PROJECTBUDGET_DATABASE_URL for PostgreSQL (ADR-010).

ARG PYTHON_VERSION=3.12

# --- build: resolve the locked environment and install the package -----------------
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS builder

# The build context has no .git, so hatch-vcs needs the version handed in. It must be the
# generic SETUPTOOLS_SCM_PRETEND_VERSION: hatch-vcs calls setuptools-scm without a distribution
# name, so the _FOR_PROJECTBUDGET form is ignored and the build falls back to 0.0.0.
ARG VERSION=0.0.0
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
# The cache mount keeps dependency wheels between builds, but it also keeps the project's own
# built wheel, whose cache key does not include the pretend version: a rebuild with a new VERSION
# would install the previous one. --reinstall-package rebuilds just this package every time.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --reinstall-package projectbudget

# --- runtime: slim Python, non-root, one volume ------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /data --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app docker/entrypoint.sh /app/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PROJECTBUDGET_DATA_DIR=/data \
    PROJECTBUDGET_HOST=0.0.0.0 \
    PROJECTBUDGET_PORT=8000

USER app
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# Migrate, then serve: migrations never race because they run before the server starts.
ENTRYPOINT ["sh", "/app/entrypoint.sh"]
