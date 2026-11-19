#!/bin/sh
# Paso propio de la imagen del API de rostros (deploy/service/Dockerfile): baja y verifica
# YuNet y SFace, así el contenedor arranca sin red. Su compose fija
# FACES_MODELS_DIR=/opt/app/models. La caché de la construcción evita bajarlos de nuevo.
set -eu
python -m faces models --salida "$APP_BUILD_CACHE/faces-models"
cp -R "$APP_BUILD_CACHE/faces-models" "$APP_ASSETS_DIR/models"
