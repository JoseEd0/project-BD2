"""Crea en `.env` las claves con que cada servicio del compose habla con el gestor.

Cada aplicación recibe dos claves: la de sus peticiones (`<PREFIJO>ENGINE_KEY`, rol `write`)
y la de sus migraciones (`<PREFIJO>MIGRATION_KEY`, rol `admin`, que solo se usa al
arrancar); la consola, la suya (`MINIGESTOR_CONSOLE_KEY`, rol `admin`). El gestor recibe
solo sus SHA-256 en `MINIGESTOR_API_KEYS`, armada aquí con todas.

Las claves que ya están en el archivo se conservan: correrlo otra vez solo añade las que
faltan y vuelve a armar `MINIGESTOR_API_KEYS`. Las demás líneas no se tocan.

    uv run python -m tools.compose_keys                         # .env en el directorio actual
    uv run python -m tools.compose_keys --service ejemplo:EJEMPLO_
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ENGINE_KEYS_VARIABLE = "MINIGESTOR_API_KEYS"
CONSOLE_KEY_VARIABLE = "MINIGESTOR_CONSOLE_KEY"
CONSOLE_SERVICE = "consola"
ADMIN_ROLE = "admin"
WRITE_ROLE = "write"
MIGRATIONS_SUFFIX = "-migraciones"
ENGINE_KEY_SUFFIX = "ENGINE_KEY"
MIGRATION_KEY_SUFFIX = "MIGRATION_KEY"
# 32 bytes aleatorios: una clave que no se adivina, así que al gestor le basta su SHA-256.
KEY_BYTES = 32
DEFAULT_ENV_FILE = Path(".env")
SERVICE_SEPARATOR = ":"

# Cada aplicación del compose con el prefijo de sus variables (el `env_prefix` de su
# configuración). El nombre es el que el gestor escribe en `service` en cada línea de log.
APPS: Mapping[str, str] = {
    "copyright": "COPYRIGHT_",
    "shop": "SHOP_",
    "papers": "RAG_",
    "faces": "FACES_",
}


class ComposeKeysError(Exception):
    """Un servicio pedido por línea de comandos no tiene la forma `nombre:PREFIJO_`."""


@dataclass(frozen=True)
class KeySlot:
    """Una clave: la variable que la guarda en claro, el servicio y el rol en el gestor."""

    variable: str
    service: str
    role: str


def key_slots(apps: Mapping[str, str]) -> list[KeySlot]:
    """La clave de la consola y las dos de cada aplicación, en ese orden."""
    slots = [KeySlot(CONSOLE_KEY_VARIABLE, CONSOLE_SERVICE, ADMIN_ROLE)]
    for name, prefix in apps.items():
        slots.append(KeySlot(prefix + ENGINE_KEY_SUFFIX, name, WRITE_ROLE))
        slots.append(KeySlot(prefix + MIGRATION_KEY_SUFFIX, name + MIGRATIONS_SUFFIX, ADMIN_ROLE))
    return slots


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def engine_keys(slots: Sequence[KeySlot], values: Mapping[str, str]) -> str:
    """El valor de `MINIGESTOR_API_KEYS`: `servicio:rol:sha256` de cada clave, por comas."""
    return ",".join(
        SERVICE_SEPARATOR.join((slot.service, slot.role, digest(values[slot.variable])))
        for slot in slots
    )


def read_assignments(lines: Sequence[str]) -> dict[str, str]:
    """Las asignaciones `NOMBRE=valor` de un `.env`, sin comentarios ni líneas vacías."""
    assignments: dict[str, str] = {}
    for line in lines:
        name, separator, value = line.partition("=")
        if separator and not line.lstrip().startswith("#"):
            assignments[name.strip()] = value.strip()
    return assignments


def update_env(lines: Sequence[str], slots: Sequence[KeySlot]) -> list[str]:
    """Las líneas de un `.env` con una clave para cada `slot` que no la tenía y
    `MINIGESTOR_API_KEYS` armada con todas; las demás líneas quedan como estaban."""
    values = read_assignments(lines)
    for slot in slots:
        if not values.get(slot.variable):
            values[slot.variable] = secrets.token_urlsafe(KEY_BYTES)
    values[ENGINE_KEYS_VARIABLE] = engine_keys(slots, values)
    managed = [slot.variable for slot in slots] + [ENGINE_KEYS_VARIABLE]
    kept = [line for line in lines if read_assignments([line]).keys().isdisjoint(managed)]
    return kept + [f"{variable}={values[variable]}" for variable in managed]


def parse_service(text: str) -> tuple[str, str]:
    """`nombre:PREFIJO_` de la línea de comandos.

    Raises:
        ComposeKeysError: si no tiene esa forma.
    """
    name, separator, prefix = text.partition(SERVICE_SEPARATOR)
    if not separator or not name or not prefix.endswith("_"):
        raise ComposeKeysError(f"servicio '{text}': se espera nombre:PREFIJO_")
    return name, prefix


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--service",
        action="append",
        default=[],
        help="otro servicio, como nombre:PREFIJO_ (el de ejemplo en la CI)",
    )
    arguments = parser.parse_args(argv)
    apps = dict(APPS) | dict(parse_service(text) for text in arguments.service)
    env_file: Path = arguments.env_file
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    env_file.write_text("\n".join(update_env(lines, key_slots(apps))) + "\n")
    env_file.chmod(0o600)
    print(f"claves de {len(apps)} aplicaciones y la consola en {env_file}")


if __name__ == "__main__":
    main()
