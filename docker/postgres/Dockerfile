# syntax=docker/dockerfile:1

ARG POSTGRES_MAJOR=17

# postgis/postgis y pgvector/pgvector no traen PostGIS y pgvector juntos, y la primera solo se
# publica para amd64; los paquetes de PGDG que trae la imagen oficial de PostgreSQL funcionan
# también en arm64.
FROM postgres:${POSTGRES_MAJOR}-bookworm

ARG POSTGRES_MAJOR
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        "postgresql-${POSTGRES_MAJOR}-postgis-3" \
        "postgresql-${POSTGRES_MAJOR}-pgvector" \
    && rm -rf /var/lib/apt/lists/*
