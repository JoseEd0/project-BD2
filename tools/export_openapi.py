"""Exporta el contrato OpenAPI de cada servicio junto a quien lo consume.

Vive fuera de `apps/` porque construye la aplicación del gestor, y ningún archivo de
`apps/` puede importar el motor (`tests/test_service_boundaries.py`). Cada contrato se
escribe en su ruta del registro, relativa a la raíz del repositorio: el del gestor junto a
su cliente de Python (`apps/client/openapi/engine.json`) y el de cada aplicación junto a su
interfaz (`apps/<app>/web/openapi/<app>.json`), que genera de él sus tipos. La CI lo corre y
falla si el resultado no coincide con lo versionado.

    uv run python -m tools.export_openapi
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from api.main import create_app
from config import EngineConfig

JSON_INDENT = 2
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


@contextmanager
def engine_app() -> Iterator[FastAPI]:
    """El API del gestor sobre un directorio de datos desechable: el contrato no depende de
    los datos, y así exportarlo no toca la base de nadie."""
    with tempfile.TemporaryDirectory() as scratch:
        app = create_app(EngineConfig(data_directory=Path(scratch)))
        try:
            yield app
        finally:
            app.state.service.close()


AppFactory = Callable[[], AbstractContextManager[FastAPI]]


@dataclass(frozen=True)
class Contract:
    """Un servicio y dónde queda su contrato, relativo a la raíz del repositorio."""

    factory: AppFactory
    path: Path


# Un servicio nuevo de `apps/` se registra aquí con una fábrica que no abra conexiones (el
# esquema sale de las rutas, no de arrancar el servicio) y la ruta junto a su interfaz:
# `apps/<app>/web/openapi/<app>.json`.
CONTRACTS: dict[str, Contract] = {
    "engine": Contract(engine_app, Path("apps/client/openapi/engine.json")),
}


def contract_text(schema: dict[str, Any]) -> str:
    """El esquema como JSON estable, para que el diff de la CI solo vea cambios reales."""
    return json.dumps(schema, indent=JSON_INDENT, ensure_ascii=False) + "\n"


def export_contracts(root: Path, contracts: dict[str, Contract] = CONTRACTS) -> list[Path]:
    """Escribe cada contrato en `root / contract.path` y devuelve las rutas escritas."""
    written = []
    for contract in contracts.values():
        path = root / contract.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with contract.factory() as app:
            path.write_text(contract_text(app.openapi()), encoding="utf-8")
        written.append(path)
    return written


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="raíz bajo la que se escriben los contratos (por omisión, la del repositorio)",
    )
    options = parser.parse_args(arguments)
    for path in export_contracts(options.root):
        print(path)


if __name__ == "__main__":
    main()
