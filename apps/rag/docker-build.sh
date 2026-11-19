#!/bin/sh
# Paso propio de la imagen del API del RAG (deploy/service/Dockerfile): baja y verifica
# multilingual-e5-small en ONNX (la variante por omisión, int8) y su tokenizador, así el
# contenedor arranca sin red. Su compose fija RAG_MODELS_DIR=/opt/app/models. La caché de la
# construcción evita bajarlos de nuevo.
set -eu
python -m rag.models --salida "$APP_BUILD_CACHE/rag-models"
mkdir -p "$APP_ASSETS_DIR/models"
cp "$APP_BUILD_CACHE"/rag-models/*.onnx "$APP_BUILD_CACHE"/rag-models/tokenizer.json \
    "$APP_ASSETS_DIR/models/"
