# Portal de aplicaciones

El producto que ve el usuario final: las cuatro aplicaciones de la Parte 5 (copyright en
audio, tienda con recomendaciones, preguntas a papers y reconocimiento de rostros) en una
sola web instalable en el celular. Es aparte de la consola del gestor
([`frontend/`](../../frontend/README.md)), que es una herramienta de base de datos; el
portal es para quien usa las aplicaciones.

Hoy ningún servicio de aplicación está desplegado: el inicio muestra cada uno como **no
disponible** porque su `/health` no responde, y cada ruta explica qué hará la aplicación
sin inventar datos. Cuando un servicio llega (#78, #83, #88, #93), su página reemplaza ese
estado vacío.

## Arquitectura

```
navegador / celular (PWA)
  └─ portal: React 19 + TypeScript estricto + Vite
       ├─ rutas         /  /copyright  /tienda  /papers  /rostros   (una por app, carga diferida)
       ├─ TanStack Query  el estado del servidor (salud de cada servicio, refrescada sola)
       ├─ openapi-fetch   peticiones tipadas con los tipos generados del OpenAPI
       └─ service worker  guarda solo el shell; /api y /apps van siempre a la red
            │
  gateway (#72) ── /api/            gestor
                └─ /apps/<servicio>  copyright · shop · papers · faces
```

| Carpeta | Contenido |
|---|---|
| `src/services/` | el catálogo de servicios (nombre, ruta, montaje en el gateway), la consulta de salud y su traducción a «disponible / no disponible» con el motivo |
| `src/api/` | el cliente tipado; `generated/` son los tipos que salen de cada contrato (no se editan) |
| `src/timing/` | lectura de `Server-Timing` y su reparto por etapa |
| `src/theme/` | preferencia de tema (sistema, claro u oscuro) |
| `src/components/` | barra, estados, tiempos por etapa, límite de error, aviso de versión nueva |
| `src/pages/` | inicio, una carpeta por aplicación y la página que se muestra mientras su servicio no tiene interfaz |
| `openapi/` | el contrato OpenAPI de cada servicio, versionado |
| `scripts/` | generación de los tipos y de los iconos |
| `tests/` | Vitest: la lógica y las rutas en jsdom |
| `e2e/` | Playwright: el flujo en Chromium contra el build, con el service worker |

Decisiones:

- **Una ruta por aplicación, en su propio fragmento.** Se cargan con `React.lazy` y no con
  el `lazy` de React Router: si un fragmento no llega (una versión nueva desplegada con la
  pestaña abierta), el de React Router deja la ruta en blanco, y el de React lo atrapa el
  límite de error de esa ruta, con la barra todavía usable.
- **El estado del servidor solo vive en TanStack Query.** Ningún componente copia la salud
  de un servicio a su estado; la consulta se refresca cada 15 s y un fallo se traduce a «no
  disponible» con su motivo (sin red, tiempo agotado, error HTTP, o una respuesta que no es
  del servicio) en lugar de reintentar.
- **La salud de un servicio sin contrato todavía** se consulta con el tipo de `/health` del
  contrato del gestor: es la ruta común de la plataforma.
- **Tema con tres opciones.** La consola alterna entre claro y oscuro; el portal sigue al
  sistema mientras el usuario no elija y sigue sus cambios en vivo, porque en el celular el
  tema cambia solo al anochecer. La misma función aplica el tema antes de pintar (Vite la
  incrusta en `index.html`) y en cada cambio. Por eso no comparte el código de la consola:
  es otra lógica, no una copia.
- **Identidad propia:** Bricolage Grotesque servida desde el bundle (funciona sin red), un
  color por aplicación y el gestor como la base de las cuatro: su borde lleva los cuatro
  colores.

## Comandos

```bash
npm --prefix apps/web ci
cp apps/web/.env.example apps/web/.env

npm --prefix apps/web run dev          # http://localhost:5173, /api reenviado al gestor
npm --prefix apps/web run build        # tipos (tsc -b) y bundle con el service worker
npm --prefix apps/web test             # Vitest
npm --prefix apps/web run build && npm --prefix apps/web run e2e   # Playwright
```

La primera vez, Playwright necesita su Chromium: `npx --prefix apps/web playwright install
chromium`.

| Variable | Para qué | Por omisión |
|---|---|---|
| `VITE_API_BASE_URL` | base bajo la que el gateway monta `/api` y `/apps/<servicio>` | el mismo origen |
| `PORTAL_DEV_ENGINE_URL` | a dónde reenvía `npm run dev` las peticiones a `/api` (sin el prefijo, como nginx) | no se reenvía |
| `PORTAL_DEV_APPS_URL` | a dónde reenvía `/apps` | no se reenvía |
| `PORTAL_E2E_PORT` | puerto de `vite preview` en los tests de Playwright | `4173`, el de Vite |

Sin `PORTAL_DEV_*` esas rutas no se reenvían y el servicio aparece no disponible, que es la
verdad: el portal valida el cuerpo de `/health` y no confunde el `index.html` de Vite con
un servicio sano.

## Contratos y tipos generados

```
servicio (FastAPI) ──app.openapi()──► openapi/<servicio>.json ──openapi-typescript──► src/api/generated/<servicio>.ts
                  tools/export_openapi.py                        scripts/generate-types.ts
```

```bash
npm --prefix apps/web run contracts    # exporta los contratos y regenera los tipos
```

- [`tools/export_openapi.py`](../../tools/export_openapi.py) construye cada aplicación
  FastAPI y escribe su `app.openapi()` como JSON estable. Vive en la raíz y no en `apps/`
  porque construye el API del gestor, y ningún archivo de `apps/` puede importar el motor
  ([`tests/test_service_boundaries.py`](../../tests/test_service_boundaries.py)). El
  gestor es el primer contrato registrado; cada servicio nuevo se añade a `CONTRACTS` con
  una fábrica que no abra conexiones.
- [`scripts/generate-types.ts`](scripts/generate-types.ts) genera un `.ts` por contrato y
  borra el de un contrato que ya no está.
- La CI (trabajo «Portal de aplicaciones») vuelve a exportar y generar, y **falla si algo
  cambió** respecto de lo versionado: un cambio en un API llega con sus tipos o no entra.

## PWA

- **Manifiesto** con nombre, `display: standalone`, colores e iconos de 192 y 512 px y uno
  `maskable` de 512. Los PNG salen de `public/icons/icon.svg` y `icon-maskable.svg` con
  `npm run icons` (resvg, la misma salida byte a byte en cada corrida) y se versionan.
- **Service worker** (Workbox, por `vite-plugin-pwa`): precarga solo lo que produce el
  build (HTML, JS, CSS, fuentes e iconos). `/api` y `/apps` van a la red (`NetworkOnly`) y
  quedan fuera del `index.html` de respaldo, así que ninguna respuesta de un servicio se
  guarda ni se sirve vieja. Lo prueban `tests/pwaConfig.test.ts` (la configuración) y
  `e2e/smoke.spec.ts` (sin red, el shell abre cualquier ruta, la caché no tiene nada de
  `/api` y una petición a `/api` falla).
- **Instalable:** Lighthouse 12 quitó su categoría PWA, así que la prueba de Playwright lee
  el manifiesto con el analizador del propio Chrome (`Page.getAppManifest`: sin errores,
  `standalone`, iconos de 192 y 512 que cargan) y comprueba que el service worker controla
  la página y responde sin red. `Page.getInstallabilityErrors` no sirve: devuelve una lista
  vacía incluso con el manifiesto roto.
- Una versión nueva no recarga la página sola: aparece «Hay una versión nueva del portal» y
  se aplica cuando el usuario pulsa «Actualizar».

El guion del tema va incrustado en `index.html`: cuando el gateway (#72) ponga una
`Content-Security-Policy`, tendrá que permitir su hash.

## Tiempos por etapa

`<StageTimings serverTiming={response.headers.get("Server-Timing")} caption="…" />` muestra
dónde se fue cada milisegundo de una respuesta. Lee la cabecera según la especificación del
W3C (comillas, escapes, mayúsculas en los parámetros, la primera aparición de cada métrica)
y descarta lo que no entiende; sin cabecera, lo dice. Las etapas que mide el gestor
([`src/api/tracing.py`](../../src/api/tracing.py): `parse`, `plan`, `exec`, `txn`,
`serialize`) llevan nombre en español, y lo que `total` mide y
ninguna etapa explica aparece como «Fuera de las etapas». El inicio lo usa con la última
comprobación de salud del gestor.
