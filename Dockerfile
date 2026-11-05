# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY demos ./demos

ARG APP_UID=10001
RUN useradd --system --uid "${APP_UID}" --home-dir /app --shell /usr/sbin/nologin minigestor \
    && mkdir /data \
    && chown minigestor /data

ENV MINIGESTOR_DATA_DIR=/data \
    MINIGESTOR_IMPORT_DIRS=/app/demos/samples

# numba, debajo de librosa, guarda lo que compila junto al código de la librería, donde el
# usuario del contenedor no puede escribir, y sin caché el primer MFCC tarda unos 18 s en
# compilar. Se compila aquí, una vez, en un directorio del usuario.
ENV NUMBA_CACHE_DIR=/app/.cache/numba
RUN python -c "import numpy, librosa; librosa.feature.mfcc(y=numpy.zeros(22050, 'float32'), sr=22050)" \
    && chown -R minigestor /app/.cache

FROM base AS bench

RUN pip install ".[postgres,plots,datasets]"
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
