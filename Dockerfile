# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.10.11

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS base

COPY --from=uv /uv /usr/local/bin/uv

# Las dependencias salen de uv.lock, las mismas versiones que en la CI. El entorno vive fuera
# de /app y uv usa el Python de la imagen, nunca uno descargado.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Primero solo las dependencias, para que cambiar el código no las vuelva a instalar. Los
# pyproject de los servicios entran montados: uv los necesita para validar el lock, pero no
# forman parte de esta imagen.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=apps,target=apps \
    uv sync --locked --no-dev --no-install-workspace --package minigestor-bd2

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=apps,target=apps \
    uv sync --locked --no-dev --no-editable --package minigestor-bd2

COPY demos ./demos

ARG APP_UID=10001
RUN useradd --system --uid "${APP_UID}" --home-dir /app --shell /usr/sbin/nologin minigestor \
    && mkdir /data \
    && chown minigestor /data

ENV MINIGESTOR_DATA_DIR=/data \
    MINIGESTOR_IMPORT_DIRS=/app/demos/samples,/app/data/datasets

# numba, debajo de librosa, guarda lo que compila junto al código de la librería, donde el
# usuario del contenedor no puede escribir, y sin caché el primer MFCC tarda unos 18 s en
# compilar. Se compila aquí, una vez, en un directorio del usuario.
ENV NUMBA_CACHE_DIR=/app/.cache/numba
RUN python -c "import numpy, librosa; librosa.feature.mfcc(y=numpy.zeros(22050, 'float32'), sr=22050)" \
    && chown -R minigestor /app/.cache

FROM base AS bench

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=apps,target=apps \
    uv sync --locked --no-dev --no-editable --package minigestor-bd2 \
        --extra postgres --extra plots --extra datasets --extra usearch
USER minigestor
ENV MPLCONFIGDIR=/tmp/matplotlib

FROM base AS engine

USER minigestor
ENV API_PORT=8000
VOLUME /data
EXPOSE ${API_PORT}

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"API_PORT\"]}/health', timeout=2)"

CMD ["sh", "-c", "exec uvicorn --factory api.main:app_from_environment --host 0.0.0.0 --port \"$API_PORT\""]
