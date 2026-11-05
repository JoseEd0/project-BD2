# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim

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
USER minigestor

ENV MINIGESTOR_DATA_DIR=/data \
    MINIGESTOR_IMPORT_DIRS=/app/demos/samples \
    API_PORT=8000

VOLUME /data
EXPOSE ${API_PORT}

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"API_PORT\"]}/health', timeout=2)"

CMD ["sh", "-c", "exec uvicorn --factory api.main:app_from_environment --host 0.0.0.0 --port \"$API_PORT\""]
