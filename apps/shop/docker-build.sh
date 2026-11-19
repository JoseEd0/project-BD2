#!/bin/sh
# Paso propio de la imagen del API de la tienda (deploy/service/Dockerfile): baja y verifica
# CLIP ViT-B/32 en ONNX (las dos torres y el tokenizador, 577 MiB), así el contenedor arranca
# sin red. Su compose fija SHOP_MODELS_DIR=/opt/app/models. La caché de la construcción evita
# bajarlo de nuevo.
set -eu
python -m shop.clip models --salida "$APP_BUILD_CACHE/shop-models"
cp -R "$APP_BUILD_CACHE/shop-models" "$APP_ASSETS_DIR/models"
