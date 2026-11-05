# syntax=docker/dockerfile:1

ARG NODE_VERSION=22
ARG NGINX_VERSION=1.27

FROM node:${NODE_VERSION}-alpine AS build

WORKDIR /frontend
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# El navegador solo habla con nginx, que reenvía /api al motor: un único origen, así que el
# bundle no necesita saber dónde vive el contenedor del API.
ARG VITE_API_URL=/api
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginxinc/nginx-unprivileged:${NGINX_VERSION}-alpine

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /frontend/dist /usr/share/nginx/html

ENV WEB_PORT=8080 \
    API_UPSTREAM=http://api:8000 \
    API_TIMEOUT=300s

EXPOSE ${WEB_PORT}

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${WEB_PORT}/" || exit 1
