# syntax=docker/dockerfile:1

# La imagen del API de cualquier aplicación de la Parte 5. Se construye con la raíz del
# repositorio como contexto y el directorio de la app en `APP_DIR`:
#   docker build --file deploy/service/Dockerfile --build-arg APP_DIR=shop .
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.10.11

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS build

COPY --from=uv /uv /usr/local/bin/uv

# Las mismas versiones que en la CI y en el gestor: las fija uv.lock. Los .pyc se compilan
# aquí porque en el contenedor el sistema de archivos es de solo lectura.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH=/opt/venv/bin:$PATH

# La carpeta de la app dentro de `apps/` y su paquete de uv; por convención,
# `apps/<app>/pyproject.toml` se llama `minigestor-<app>`.
ARG APP_DIR
ARG APP_PROJECT=minigestor-${APP_DIR}

WORKDIR /workspace

# Primero solo las dependencias, para que cambiar el código de la app no las reinstale.
# uv necesita los pyproject de todos los miembros para validar el lock; entran montados.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=apps,target=apps \
    test -n "${APP_DIR}" \
    && uv sync --locked --no-dev --no-install-workspace --package "${APP_PROJECT}"

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=apps,target=apps,rw \
    uv sync --locked --no-dev --no-editable --package "${APP_PROJECT}"

# El paso de construcción propio de la app, si lo declara en `apps/<app>/docker-build.sh`:
# corre con la app instalada y deja en APP_ASSETS_DIR lo que el contenedor necesita sin red
# (los modelos de rostros, por ejemplo). APP_BUILD_CACHE sobrevive entre construcciones, para
# no volver a bajar lo mismo cada vez que cambia el código.
ENV APP_ASSETS_DIR=/opt/app \
    APP_BUILD_CACHE=/root/.cache/app-build
RUN --mount=type=cache,target=/root/.cache/app-build \
    --mount=type=bind,source=apps,target=apps \
    mkdir -p "${APP_ASSETS_DIR}" \
    && if [ -f "apps/${APP_DIR}/docker-build.sh" ]; then sh "apps/${APP_DIR}/docker-build.sh"; fi

FROM python:${PYTHON_VERSION}-slim AS service

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    APP_ASSETS_DIR=/opt/app

COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/app /opt/app

ARG APP_UID=10001
RUN useradd --system --uid "${APP_UID}" --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin service

# El paquete de Python de la app; por convención, el nombre de su carpeta. Expone
# `<paquete>.api:create_app` (la fábrica de la aplicación) y `<paquete>.seed` (su carga).
ARG APP_DIR
ARG APP_PACKAGE=${APP_DIR}
ENV SERVICE_APP=${APP_PACKAGE}.api:create_app \
    SERVICE_SEED_MODULE=${APP_PACKAGE}.seed \
    SERVICE_PORT=8000 \
    SERVICE_DRAIN_SECONDS=10 \
    SERVICE_FORWARDED_ALLOW_IPS=*

USER service
WORKDIR /tmp
EXPOSE ${SERVICE_PORT}

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"SERVICE_PORT\"]}/health', timeout=2)"

# `exec` deja a uvicorn como PID 1: con el SIGTERM de `docker stop` deja de aceptar
# conexiones y espera SERVICE_DRAIN_SECONDS a las que siguen abiertas. Solo el nginx de la
# app le habla, así que confía en su X-Forwarded-For para registrar la IP del cliente.
CMD ["sh", "-c", "exec uvicorn --factory \"$SERVICE_APP\" --host 0.0.0.0 --port \"$SERVICE_PORT\" --timeout-graceful-shutdown \"$SERVICE_DRAIN_SECONDS\" --proxy-headers --forwarded-allow-ips \"$SERVICE_FORWARDED_ALLOW_IPS\""]
