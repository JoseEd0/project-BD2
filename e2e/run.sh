#!/usr/bin/env bash
# La suite e2e contra el compose completo: el gestor, la consola, Redis y las cuatro
# aplicaciones de verdad, con datos diminutos, el LLM simulado y la cámara simulada de
# Chromium con una foto de la NASA.
#
#   e2e/run.sh          # prepara los datos, levanta, siembra, prueba y apaga (con sus volúmenes)
#   e2e/run.sh up       # solo prepara, levanta y siembra
#   e2e/run.sh test     # solo la suite, contra lo que dejó `up`
#   e2e/run.sh logs     # el registro de todos los servicios
#   e2e/run.sh down     # apaga y borra los volúmenes de este proyecto
#
# Usa su propio proyecto de compose (COMPOSE_PROJECT_NAME, por omisión minigestor-e2e), sus
# claves y sus datos en e2e/.state y puertos 28xxx, así que no toca un compose ya levantado.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"

export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-minigestor-e2e}
state=${E2E_STATE_DIR:-$repo/e2e/.state}
datasets=$state/datasets
export MINIGESTOR_DATASETS_DIR=$datasets
export PAPERS_DATASETS_DIR=$datasets
# Una carpeta sin certificado: cada web sirve solo HTTP, y localhost ya es un origen seguro.
export MINIGESTOR_CERTS_DIR=$state/certs
export RAG_LLM_PROVIDER=simulated
export E2E_CAMERA_VIDEO=$state/camera.y4m
export MINIGESTOR_WEB_PORT=${MINIGESTOR_WEB_PORT:-28080}
export COPYRIGHT_WEB_PORT=${COPYRIGHT_WEB_PORT:-28101}
export SHOP_WEB_PORT=${SHOP_WEB_PORT:-28102}
export PAPERS_WEB_PORT=${PAPERS_WEB_PORT:-28103}
export FACES_WEB_PORT=${FACES_WEB_PORT:-28104}
export COPYRIGHT_WEB_TLS_PORT=${COPYRIGHT_WEB_TLS_PORT:-28401}
export SHOP_WEB_TLS_PORT=${SHOP_WEB_TLS_PORT:-28402}
export PAPERS_WEB_TLS_PORT=${PAPERS_WEB_TLS_PORT:-28403}
export FACES_WEB_TLS_PORT=${FACES_WEB_TLS_PORT:-28404}
WAIT_TIMEOUT_SECONDS=${E2E_WAIT_TIMEOUT_SECONDS:-900}
FACE_PHOTO=apps/faces/tests/fixtures/collins-1998.jpg
# La cara del retrato, como la selfie del humo de rostros en la CI: así llena el óvalo.
FACE_CROP="60 30 250 250"

compose() {
    local env_file=()
    if [ -f "$state/keys.env" ]; then env_file=(--env-file "$state/keys.env"); fi
    docker compose ${env_file[@]+"${env_file[@]}"} -f compose.yaml -f e2e/compose.yaml "$@"
}

prepare() {
    mkdir -p "$datasets" "$MINIGESTOR_CERTS_DIR"
    uv run --locked --package minigestor-shop python -m apps.shop.tests.tiny_dataset \
        "$datasets/amazon_musical_instruments"
    uv run --locked --package minigestor-shop python -m apps.faces.tests.fake_camera \
        "$FACE_PHOTO" "$E2E_CAMERA_VIDEO" $FACE_CROP
    PYTHONPATH=apps/copyright/src python3 -m songcheck.sample --data-dir "$datasets"
    python3 -m apps.rag.tests.tiny_corpus "$datasets"
    python3 -m tools.compose_keys --env-file "$state/keys.env"
}

up() {
    prepare
    compose up --build --detach --wait --wait-timeout "$WAIT_TIMEOUT_SECONDS"
    for seed in shop-seed copyright-seed papers-seed; do
        compose run --rm "$seed"
    done
}

suite() {
    npm --prefix e2e ci --no-audit --no-fund
    npx --prefix e2e playwright install ${CI:+--with-deps} chromium
    npm --prefix e2e test
}

down() {
    compose down --volumes --remove-orphans
}

case ${1:-all} in
    up) up ;;
    test) suite ;;
    logs) compose logs ;;
    down) down ;;
    all)
        trap down EXIT
        up
        suite
        ;;
    *)
        echo "uso: e2e/run.sh [up|test|logs|down]" >&2
        exit 2
        ;;
esac
