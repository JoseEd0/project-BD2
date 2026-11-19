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
  shop/            tienda con recomendaciones (opción B): dataset, catálogo en el gestor y CLIP
  rag/             RAG sobre papers (opción A): por ahora, el corpus de arXiv y sus fragmentos
  faces/           rostros (opción D): detección con YuNet, alineación y embedding con SFace
  web-kit/         @minigestor/web-kit: lógica sin estilos que comparten las interfaces
    template/      plantilla de la interfaz de una app: Vite y React
    docker/        la imagen común de las interfaces: build y nginx endurecido
  <app>/           el API de la aplicación (FastAPI), miembro del espacio de trabajo de uv
    web/           su interfaz, creada desde la plantilla, con su contrato en openapi/
    compose.yaml   su API, su interfaz, su carga y su túnel, incluidos desde el compose raíz
    docker-build.sh  opcional: el paso propio de la imagen de su API (modelos, por ejemplo)
```

| Aplicación | Carpeta | Contenedores | HTTP / HTTPS | En el compose |
|---|---|---|---|---|
| Copyright en audio | `apps/copyright/` | `copyright-api`, `copyright-web` | 8101 / 8401 | pendiente: sin API todavía |
| Tienda con recomendaciones | `apps/shop/` | `shop-api`, `shop-web` | 8102 / 8402 | sí; la interfaz es la plantilla hasta #83 |
| Preguntas a papers (RAG) | `apps/rag/` | `papers-api`, `papers-web` | 8103 / 8403 | pendiente: sin API todavía |
| Reconocimiento de rostros | `apps/faces/` | `faces-api`, `faces-web` | 8104 / 8404 | pendiente: sin API todavía |
| Ejemplo del kit (perfil `example`) | `deploy/example/` | `example-api`, `example-web` | 8100 / 8400 | sí, para probar la topología |

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

## Desplegar una app

```
navegador ─► :810x <app>-web (nginx) ─► <app>-api ×N ─┐  red <app>
                                                      ▼  red data (interna)
                         :8080 console ─────────────► engine ── volumen minigestor-data
                                         <app>-api ─► redis  (red cache, interna; papers y faces)
```

El gestor (`engine`) no publica puertos y vive en la red interna `data`; solo la consola y
las API llegan a él, cada una con su clave. Cada `<app>-web` está solo en la red de su app:
no puede hablar con el gestor ni con otra app. Lo común de cada servicio está en
[`deploy/app.compose.yaml`](../deploy/app.compose.yaml) (solo lectura, sin capacidades,
`tmpfs`, topes de memoria, apagado ordenado) y cada app lo extiende.

**Convenciones del paquete.** `apps/<app>/pyproject.toml` se llama `minigestor-<app>`, su
paquete de Python es `<app>`, `<app>.api:create_app()` es la fábrica de su FastAPI (sin
argumentos: lee su configuración del entorno) y `python -m <app>.seed` es su carga inicial,
idempotente. Si el paquete se llama distinto que la carpeta, `APP_PACKAGE` lo dice. La
imagen ([`deploy/service/Dockerfile`](../deploy/service/Dockerfile)) instala solo ese
miembro desde `uv.lock`, corre como usuario sin privilegios y sirve con uvicorn en el
`8000`. Si la app necesita algo al construir (los modelos de rostros), lo declara en
`apps/<app>/docker-build.sh`: corre con la app instalada y deja en `$APP_ASSETS_DIR`
(`/opt/app`) lo que el contenedor necesita sin red; `$APP_BUILD_CACHE` sobrevive entre
construcciones.

**Pasos para conectar una app** (la tienda, por ejemplo), cuando tiene su API:

1. Crear `apps/shop/compose.yaml` copiando [`deploy/example/compose.yaml`](../deploy/example/compose.yaml)
   y ajustando nombres, prefijo y puertos:

   ```yaml
   services:
     shop-api:
       extends: { file: ../../deploy/app.compose.yaml, service: api }
       build: { args: { APP_DIR: shop } }
       image: minigestor-shop-api
       environment: &shop-environment
         SHOP_ENGINE_URL: http://engine:8000
         SHOP_ENGINE_KEY: ${SHOP_ENGINE_KEY:-}
         SHOP_MIGRATION_KEY: ${SHOP_MIGRATION_KEY:-}
         SHOP_LOG_LEVEL: ${SHOP_LOG_LEVEL:-INFO}
       networks: [data, shop]          # papers y faces, además: cache
       depends_on: { engine: { condition: service_healthy } }

     shop-web:
       extends: { file: ../../deploy/app.compose.yaml, service: web }
       build: { args: { APP_DIR: shop/web } }
       image: minigestor-shop-web
       environment:
         API_UPSTREAM: http://shop-api:8000
         # rostros: WEB_PERMISSIONS_POLICY "camera=(self), …", API_MAX_BODY_SIZE, API_RATE_LIMIT
       ports:
         - ${SHOP_WEB_PORT:-8102}:8080
         - ${SHOP_WEB_TLS_PORT:-8402}:8443
       networks: [shop]
       depends_on: { shop-api: { condition: service_healthy } }

     shop-seed:
       extends: { file: ../../deploy/app.compose.yaml, service: seed }
       profiles: [seed]
       image: minigestor-shop-api
       environment: *shop-environment
       networks: [data]
       depends_on: { shop-api: { condition: service_healthy } }

     shop-tunnel:
       extends: { file: ../../deploy/app.compose.yaml, service: tunnel }
       profiles: [tunnel]
       command: ["tunnel", "--no-autoupdate", "--url", "http://shop-web:8080"]
       networks: [shop]
       depends_on: { shop-web: { condition: service_healthy } }

   networks:
     shop: {}                          # solo la propia; data y cache están en el raíz
   ```

   Sin interfaz todavía, `shop-web` puede usar `APP_DIR: web-kit/template`.
2. Añadir `apps/shop/compose.yaml` al `include:` del [`compose.yaml`](../compose.yaml) raíz.
3. Si la app no está en `APPS` de [`tools/compose_keys.py`](../tools/compose_keys.py), sumarla
   con su prefijo, y volver a correr `python -m tools.compose_keys` (conserva las claves que ya
   hay y añade las nuevas a `MINIGESTOR_API_KEYS`).
4. Sus variables en [`.env.example`](../.env.example) y una comprobación por su URL en el
   trabajo `compose` de la CI, como las del ejemplo.

Límites del nginx por app: RAG y rostros bajan `API_RATE_LIMIT`/`API_RATE_BURST` (cada
pregunta al LLM cuesta; cada foto, CPU) y fijan `API_MAX_BODY_SIZE` a lo que suben; las
variables están en el [README del kit web](web-kit/README.md#imagen-y-nginx).

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

Los tests de [`faces/`](faces/README.md) y de [`shop/`](shop/README.md) que corren modelos
los buscan en `FACES_MODELS_DIR` y `SHOP_MODELS_DIR`; sin la variable se saltan, y la CI la
define y los baja antes.
