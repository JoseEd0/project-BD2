# syntax=docker/dockerfile:1

# La imagen de la interfaz de cualquier aplicación: su build sobre el kit, servido por un nginx
# sin privilegios que reenvía /api solo a la API de la app. Se construye con `apps/` como
# contexto y la carpeta de la interfaz en `APP_DIR`:
#   docker build --file apps/web-kit/docker/Dockerfile --build-arg APP_DIR=shop/web apps
ARG NODE_VERSION=22.23.4
ARG NGINX_VERSION=1.27

FROM node:${NODE_VERSION}-alpine AS build

ARG APP_DIR

WORKDIR /apps
# Solo las fuentes del kit: sus importaciones se resuelven en el node_modules de la app.
COPY web-kit/package.json web-kit/
COPY web-kit/src web-kit/src
COPY web-kit/scripts web-kit/scripts
COPY ${APP_DIR}/package.json ${APP_DIR}/package-lock.json ${APP_DIR}/
RUN test -n "${APP_DIR}" && npm --prefix "${APP_DIR}" ci --no-audit --no-fund

COPY ${APP_DIR} ${APP_DIR}
# El navegador solo habla con el nginx de la app, que reenvía /api a su API: un único
# origen, así que el bundle no necesita saber dónde vive el contenedor del API.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm --prefix "${APP_DIR}" run build && mv "${APP_DIR}/dist" /dist

# La CSP permite los guiones en línea de este build y ningún otro: sus hashes se miden sobre
# el index.html que se va a servir.
RUN printf 'set $csp_script_hashes "%s";\n' \
        "$(node web-kit/scripts/csp-script-hashes.ts /dist/index.html)" > /csp.conf

FROM nginxinc/nginx-unprivileged:${NGINX_VERSION}-alpine

COPY web-kit/docker/nginx/templates /etc/nginx/templates
COPY web-kit/docker/nginx/tls.conf.template /etc/nginx/app/tls.conf.template
COPY --chmod=755 web-kit/docker/nginx/16-api-upstream.envsh /docker-entrypoint.d/16-api-upstream.envsh
COPY --chmod=755 web-kit/docker/nginx/25-tls.sh /docker-entrypoint.d/25-tls.sh
COPY --from=build /csp.conf /etc/nginx/app/csp.conf
COPY --from=build /dist /usr/share/nginx/html

# Lo que cambia de una app a otra lo pone su compose: a qué API reenvía, cuánto puede subir,
# cuántas peticiones por segundo admite de cada IP, qué permisos del navegador pide y de qué
# otros orígenes carga imágenes (las teselas de un mapa).
ENV WEB_PORT=8080 \
    WEB_TLS_PORT=8443 \
    API_UPSTREAM=http://api:8000 \
    API_TIMEOUT=60s \
    API_KEEPALIVE_CONNECTIONS=32 \
    API_KEEPALIVE_TIMEOUT=4s \
    API_MAX_BODY_SIZE=1m \
    API_RATE_LIMIT=20r/s \
    API_RATE_BURST=40 \
    WEB_PERMISSIONS_POLICY="camera=(), microphone=(), geolocation=()" \
    WEB_IMG_SOURCES="" \
    HSTS_MAX_AGE=31536000 \
    TLS_CERTIFICATE=/etc/nginx/certs/cert.pem \
    TLS_CERTIFICATE_KEY=/etc/nginx/certs/key.pem \
    DNS_TTL=10s \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

EXPOSE ${WEB_PORT} ${WEB_TLS_PORT}

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${WEB_PORT}/" || exit 1
