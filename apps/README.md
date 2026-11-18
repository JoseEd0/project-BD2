# Servicios de la Parte 5

Cada aplicación del Anexo A es un servicio aparte que usa el gestor como lo haría con
cualquier base de datos: **solo por su API REST**. El gestor es un contenedor con estado en
una red interna; cada servicio es otro contenedor, sin estado, que escala con réplicas.

```
apps/
  client/      minigestor_client: cliente tipado del API del gestor
  kit/         appkit: configuración, logs, salud, errores y descargas verificadas
  shop/        tienda con recomendaciones (opción B): por ahora, su dataset
  web/         portal de las cuatro aplicaciones: React + TypeScript, instalable (PWA)
```

El portal ([`web/`](web/README.md)) no es un servicio de Python sino la web que los usa: un
proyecto npm propio, fuera del espacio de trabajo de uv, que consulta el `/health` de cada
servicio y genera sus tipos del OpenAPI que cada uno publica.

## Reglas

- **Ningún servicio importa el código del gestor** (`src/`). Lo comprueba
  [`tests/test_service_boundaries.py`](../tests/test_service_boundaries.py) sobre cada
  archivo de `apps/`.
- Cada servicio es un miembro del espacio de trabajo de uv, con su `pyproject.toml`,
  `src/<paquete>/` y `tests/`. Se registra en `[tool.uv.workspace]` del `pyproject.toml`
  raíz, y sus versiones quedan fijadas en el `uv.lock` común.
- Los tests de un servicio se llaman `test_<servicio>_*.py`: pytest importa cada archivo por
  su nombre, así que dos servicios no pueden tener un `test_api.py` cada uno.
- `ruff` y `mypy --strict` cubren `apps/*/src` igual que el gestor. A mypy cada servicio
  se le añade por nombre en `files` de `[tool.mypy]`, como en `[tool.uv.workspace]`: un
  patrón alcanzaría `apps/web/src`, que es TypeScript.
- Cada servicio registra su contrato en
  [`tools/export_openapi.py`](../tools/export_openapi.py) y lo exporta con
  `npm --prefix apps/web run contracts`, que también genera los tipos del portal; la CI
  falla si el contrato versionado no coincide con el del código.

## Descargas de modelos y datasets

Un servicio que necesita un archivo grande —un modelo, un dataset— lo fija con su URL, su
SHA-256 y su tamaño en un `appkit.download.RemoteFile` y lo baja con `fetch_verified`:

- la descarga se escribe como `<archivo>.parcial` y, si se corta, sigue desde su último
  byte con `Range` (hasta cinco intentos);
- solo un archivo completo y con la suma fijada recibe el nombre final; uno con otra suma
  se borra y se lanza `ChecksumMismatchError`;
- si el archivo ya está y su suma coincide, no se baja nada, así que el mismo comando sirve
  para la CI, la construcción de una imagen y una máquina de desarrollo.

## Entorno

```bash
uv sync --all-packages          # gestor, servicios y herramientas de desarrollo
uv run pytest apps -q           # solo los tests de los servicios
```

Los datos de cada servicio se bajan con su propio comando a la carpeta de datasets, que
nunca se versiona; el de la tienda está en [`shop/README.md`](shop/README.md).
