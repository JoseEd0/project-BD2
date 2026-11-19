#!/bin/sh
# Corre después de que la imagen de nginx renderiza las plantillas (20-envsubst-on-templates):
# con un certificado y su clave montados, añade el servidor HTTPS; sin ellos, el sitio queda
# solo por HTTP, que basta en el mismo equipo (localhost es un origen seguro).
set -eu

if [ -r "$TLS_CERTIFICATE" ] && [ -r "$TLS_CERTIFICATE_KEY" ]; then
    envsubst '${WEB_TLS_PORT} ${TLS_CERTIFICATE} ${TLS_CERTIFICATE_KEY}' \
        < /etc/nginx/app/tls.conf.template > /etc/nginx/conf.d/tls.conf
    echo "$0: HTTPS en el puerto ${WEB_TLS_PORT} con ${TLS_CERTIFICATE}"
else
    echo "$0: sin certificado legible en ${TLS_CERTIFICATE}: solo HTTP"
fi
