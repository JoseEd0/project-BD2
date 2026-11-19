#!/bin/sh
# Certificado local para que cada <app>-web sirva HTTPS: la cámara y el micrófono del celular
# solo funcionan en un origen seguro. Vale para localhost, el nombre del equipo y cada IP de
# la red local, así que el celular puede entrar por https://<ip>:<puerto HTTPS de la app>.
#
#   deploy/certs.sh [carpeta] [nombre o IP extra ...]
#
# Con mkcert instalado, el certificado lo firma su CA local (`mkcert -install` una vez; en el
# celular se instala esa CA para que no avise). Sin mkcert, es autofirmado con openssl y el
# navegador pide aceptarlo una vez. La carpeta, por omisión MINIGESTOR_CERTS_DIR o
# ./data/certs, nunca se versiona.
set -eu

DEFAULT_CERTS_DIR=./data/certs
VALID_DAYS=825
KEY_BITS=2048

certs_dir=${1:-${MINIGESTOR_CERTS_DIR:-$DEFAULT_CERTS_DIR}}
[ $# -gt 0 ] && shift
extra_names="$*"

lan_addresses() {
    if command -v ip >/dev/null 2>&1; then
        ip -4 -o addr show scope global | awk '{ split($4, a, "/"); print a[1] }'
    else
        ifconfig 2>/dev/null | awk '$1 == "inet" && $2 != "127.0.0.1" { print $2 }'
    fi
}

names="localhost 127.0.0.1 ::1 $(hostname) $(lan_addresses | tr '\n' ' ') $extra_names"
mkdir -p "$certs_dir"
cert="$certs_dir/cert.pem"
key="$certs_dir/key.pem"

if command -v mkcert >/dev/null 2>&1; then
    # shellcheck disable=SC2086 # cada nombre es un argumento
    mkcert -cert-file "$cert" -key-file "$key" $names
else
    san=""
    for name in $names; do
        case "$name" in
            *:*) entry="IP:$name" ;;
            *[!0-9.]*) entry="DNS:$name" ;;
            *) entry="IP:$name" ;;
        esac
        san="${san:+$san,}$entry"
    done
    config=$(mktemp)
    trap 'rm -f "$config"' EXIT
    cat > "$config" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = minigestor local
[ext]
subjectAltName = $san
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EOF
    openssl req -x509 -newkey "rsa:$KEY_BITS" -nodes -sha256 -days "$VALID_DAYS" \
        -keyout "$key" -out "$cert" -config "$config"
fi

# nginx corre en el contenedor con otro usuario y tiene que leer la clave montada. Es un
# certificado de desarrollo para la red local, no un secreto del despliegue.
chmod 644 "$cert" "$key"
echo "certificado en $cert para: $names"
