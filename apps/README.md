# Servicios de la Parte 5

Cada aplicación del Anexo A es un producto aparte que usa el gestor como lo haría con
cualquier base de datos: **solo por su API REST**. El gestor es un contenedor con estado en
una red interna; cada aplicación tiene su API (otro contenedor, sin estado, que escala con
réplicas) y su interfaz (un nginx que la sirve y reenvía `/api` solo a esa API), en su
propio puerto y con su propia identidad visual.

```
apps/
  client/          minigestor_client: cliente tipado del API del gestor; openapi/engine.json
  kit/             appkit: configuración, logs, trazas, salud, errores, migraciones y descargas
  shop/            tienda con recomendaciones (opción B): por ahora, su dataset
  faces/           rostros (opción D): detección con YuNet, alineación y embedding con SFace
  web-kit/         @minigestor/web-kit: lógica sin estilos que comparten las interfaces
    template/      plantilla de la interfaz de una app: Vite, React, Docker y nginx
  <app>/           el API de la aplicación (FastAPI), miembro del espacio de trabajo de uv
    web/           su interfaz, creada desde la plantilla, con su contrato en openapi/
    compose.yaml   su API y su interfaz, incluidos desde el compose raíz (#72)
```

| Aplicación | Carpeta | Puerto de su interfaz |
|---|---|---|
| Copyright en audio | `apps/copyright/` | 8101 |
| Tienda con recomendaciones | `apps/shop/` | 8102 |
| Preguntas a papers (RAG) | `apps/rag/` | 8103 |
| Reconocimiento de rostros | `apps/faces/` | 8104 |

Las interfaces comparten lógica (cliente tipado, salud, `Server-Timing`, tema, PWA) a través
del [kit web](web-kit/README.md), nunca apariencia. Son proyectos npm propios, fuera del
espacio de trabajo de uv.

## Hablar con el gestor

Un servicio usa el gestor como una app usa PostgreSQL: con un driver y una clave.

- [`client/`](client/README.md) (`minigestor_client`) es ese driver: síncrono y asíncrono,
  con parámetros `$n`, vectores y puntos, transacciones, carga masiva por NDJSON, consultas
  multimedia, errores tipados y reintentos solo de lo que no escribe. Lo prueban tests de
  contrato contra el API real del gestor (`tests/client/`) y contra su contrato versionado.
- [`kit/`](kit/README.md) (`appkit`) arma la aplicación FastAPI del servicio con
  `create_service_app`: configuración del entorno que falla al arrancar si falta algo, logs
  JSON con la forma de los del gestor, `X-Request-ID` de punta a punta y `Server-Timing`,
  errores uniformes, `/health` y `/ready`, y migraciones `CREATE … IF NOT EXISTS` al arrancar
  con su propia clave `admin`.

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
  patrón alcanzaría `apps/web-kit/src`, que es TypeScript.
- Cada servicio registra su contrato en
  [`tools/export_openapi.py`](../tools/export_openapi.py) con la ruta junto a su interfaz
  (`apps/<app>/web/openapi/<app>.json`) y lo exporta con
  `npm --prefix apps/<app>/web run contracts`, que también genera los tipos de la interfaz;
  la CI falla si un contrato versionado o sus tipos no coinciden con el código.

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

Los tests de [`faces/`](faces/README.md) que corren los modelos los buscan en
`FACES_MODELS_DIR`; sin la variable se saltan, y la CI la define y los baja antes.
