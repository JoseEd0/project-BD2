"""Exporta el contrato OpenAPI de cada servicio, del que el portal genera sus tipos.

Vive fuera de `apps/` porque construye la aplicación del gestor, y ningún archivo de
`apps/` puede importar el motor (`tests/test_service_boundaries.py`). Cada contrato queda
en `<salida>/<servicio>.json`; `npm --prefix apps/web run contracts` lo corre y genera los
tipos, y la CI falla si el resultado no coincide con lo versionado.

    uv run python -m tools.export_openapi --output apps/web/openapi
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from api.main import create_app
from config import EngineConfig

JSON_INDENT = 2
CONTRACT_SUFFIX = ".json"


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

# Un servicio nuevo de `apps/` se registra aquí con una fábrica que no abra conexiones: el
# esquema sale de las rutas, no de arrancar el servicio.
CONTRACTS: dict[str, AppFactory] = {"engine": engine_app}


def contract_text(schema: dict[str, Any]) -> str:
    """El esquema como JSON estable, para que el diff de la CI solo vea cambios reales."""
    return json.dumps(schema, indent=JSON_INDENT, ensure_ascii=False) + "\n"


def export_contracts(output: Path, contracts: dict[str, AppFactory] = CONTRACTS) -> list[Path]:
    """Escribe un `<servicio>.json` por contrato en `output` y devuelve las rutas escritas."""
    output.mkdir(parents=True, exist_ok=True)
    written = []
    for name, factory in contracts.items():
        with factory() as app:
            path = output / f"{name}{CONTRACT_SUFFIX}"
            path.write_text(contract_text(app.openapi()), encoding="utf-8")
            written.append(path)
    return written


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--output", type=Path, required=True, help="carpeta de los contratos")
    options = parser.parse_args(arguments)
    for path in export_contracts(options.output):
        print(path)


if __name__ == "__main__":
    main()
