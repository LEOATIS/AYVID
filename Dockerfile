# syntax=docker/dockerfile:1.7
# AYVID API + worker base image. Slim, non-root, layer-cached.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential gcc curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
# Wheel setuptools + wheel up front so the runtime `pip install
# --no-index` step can satisfy the PEP 517 build-system requirement
# without reaching PyPI. Without this, pip 23+ tries to spin up an
# isolated venv and fails because /wheels has the project's runtime
# deps but not its build-time deps.
RUN pip install --upgrade pip && \
    pip wheel --wheel-dir /wheels "setuptools>=61.0" "wheel" && \
    pip wheel --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    AYVID_HOME=/app

RUN apt-get update && apt-get install --no-install-recommends -y \
        curl tini && \
    rm -rf /var/lib/apt/lists/* && \
    addgroup --system --gid 1001 ayvid && \
    adduser  --system --uid 1001 --gid 1001 --home /app ayvid

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY pyproject.toml ./
# `--no-build-isolation` because we're installing offline from /wheels;
# pip would otherwise try to fetch setuptools from PyPI inside a fresh
# venv. The wheel for setuptools is already in /wheels (above) so the
# system Python can resolve the build backend locally.
RUN pip install --no-index --find-links=/wheels --no-build-isolation \
        "setuptools>=61.0" "wheel" && \
    pip install --no-index --find-links=/wheels --no-build-isolation . && \
    rm -rf /wheels

COPY --chown=ayvid:ayvid . .

RUN mkdir -p /app/live /app/document/backtests /app/.cache && \
    chown -R ayvid:ayvid /app

USER ayvid

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "backend.core.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
