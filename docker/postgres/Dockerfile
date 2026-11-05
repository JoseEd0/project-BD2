# syntax=docker/dockerfile:1

ARG POSTGRES_MAJOR=17

# postgis/postgis solo se publica para amd64; el paquete de PGDG que trae la imagen oficial
# de PostgreSQL funciona también en arm64.
FROM postgres:${POSTGRES_MAJOR}-bookworm

ARG POSTGRES_MAJOR
RUN apt-get update \
    && apt-get install --yes --no-install-recommends "postgresql-${POSTGRES_MAJOR}-postgis-3" \
    && rm -rf /var/lib/apt/lists/*
