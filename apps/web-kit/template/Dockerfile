# syntax=docker/dockerfile:1

# Se construye con `apps/` como contexto, para que la imagen lleve el kit además de la app:
#   docker build --file apps/<app>/web/Dockerfile --build-arg APP_DIR=<app>/web apps
ARG NODE_VERSION=22.23.4
ARG NGINX_VERSION=1.27

FROM node:${NODE_VERSION}-alpine AS build

# La carpeta de la interfaz dentro de `apps/`; al copiar la plantilla, `<app>/web`.
ARG APP_DIR=web-kit/template

WORKDIR /apps
# Solo las fuentes del kit: sus importaciones se resuelven en el node_modules de la app.
COPY web-kit/package.json web-kit/
COPY web-kit/src web-kit/src
COPY ${APP_DIR}/package.json ${APP_DIR}/package-lock.json ${APP_DIR}/
RUN npm --prefix "${APP_DIR}" ci --no-audit --no-fund

COPY ${APP_DIR} ${APP_DIR}
# El navegador solo habla con el nginx de la app, que reenvía /api a su API: un único
# origen, así que el bundle no necesita saber dónde vive el contenedor del API.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm --prefix "${APP_DIR}" run build && mv "${APP_DIR}/dist" /dist

FROM nginxinc/nginx-unprivileged:${NGINX_VERSION}-alpine

ARG APP_DIR=web-kit/template
COPY ${APP_DIR}/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /dist /usr/share/nginx/html

ENV WEB_PORT=8080 \
    API_UPSTREAM=http://api:8000 \
    API_TIMEOUT=60s

EXPOSE ${WEB_PORT}

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${WEB_PORT}/" || exit 1
