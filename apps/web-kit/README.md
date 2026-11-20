# Kit web de las aplicaciones

`@minigestor/web-kit` es la lógica que comparten las interfaces de las aplicaciones de la
Parte 5. Cada aplicación es un producto aparte, con su API, su interfaz, su contenedor, su
puerto y **su propia identidad visual**; por eso el kit no trae estilos ni componentes
visuales: solo funciones y hooks sin apariencia. Cómo se ve cada cosa lo decide cada app en
su `PRODUCT.md` y su `DESIGN.md`.

```
navegador ─► :8101 copyright-web ─► copyright-api ─┐
          ─► :8102 shop-web      ─► shop-api      ─┤   cada <app>-web: React + TS sobre este kit,
          ─► :8103 papers-web    ─► papers-api    ─┤   servido por un nginx que reenvía /api
          ─► :8104 faces-web     ─► faces-api     ─┤   solo a su propia API
                                                   ▼
                                            engine (red interna)
```

## Qué trae

| Entrada | Contenido |
|---|---|
| `@minigestor/web-kit` | `createServiceClient<paths>()`: cliente tipado (openapi-fetch) sobre los `paths` del contrato de la app, que pone un `X-Request-ID` en cada petición; `probeHealth`, `healthQuery` y el mapeo de `/health` a «disponible» o «no disponible» con su motivo (`unreachable`, `timeout`, `http-error`, `unexpected-body`); `parseServerTiming`, `stageBreakdown` y `useStageTimings`, que devuelve las etapas, su parte del total y el resto sin atribuir; la preferencia de tema (sistema, claro, oscuro) recordada y aplicada en vivo con `useThemePreference` |
| `@minigestor/web-kit/vite` | `webKit()`, los plugins de Vite que toda interfaz añade: la resolución del kit y el guion de arranque del tema incrustado en `index.html`; `themeBootScriptHash()` e `inlineScriptHash()` para la CSP |
| `@minigestor/web-kit/pwa` | `createPwaOptions({ manifest, apiPrefixes })`: el manifiesto es de cada app; el service worker precarga solo el shell, y las rutas del API (`/api` por omisión) van siempre a la red (`NetworkOnly`) y quedan fuera del `index.html` de respaldo |
| `@minigestor/web-kit/testing` | `stubFetch`, `jsonResponse` e `installMatchMedia` para los tests de Vitest |
| `scripts/generate-types.ts` | genera `src/api/generated/<app>.ts` desde `openapi/<app>.json` con `openapi-typescript` |

Los textos que ve el usuario (cómo se dice cada motivo de «no disponible», el nombre de cada
etapa, el formato de los milisegundos) no están en el kit: son tono, y el tono es de cada app.

## Cómo se consume

Cada interfaz depende del kit con `"@minigestor/web-kit": "file:../../web-kit"` y **compila
sus fuentes TypeScript**: el kit no tiene paso de build. Se eligió así porque es lo más
simple que funciona igual en desarrollo, en la CI y en la imagen de Docker:

- No hay un `dist` del kit que construir antes, ni que pueda quedar viejo respecto de sus
  fuentes; un cambio en el kit llega a la app en el siguiente `npm run dev` o `build`.
- npm instala el kit como un enlace simbólico. Las dependencias que el kit comparte con la
  app (React, openapi-fetch, Vite, vite-plugin-pwa) son `peerDependencies`: las pone la app.
- `webKit()` configura Vite y Vitest para esto: `resolve.preserveSymlinks` (las
  importaciones del kit se buscan en el `node_modules` de la app, así que la imagen de
  Docker solo instala las de la app), `resolve.dedupe` de React y openapi-fetch (si el kit
  tiene su propio `node_modules` para sus tests, no entra una segunda copia de React) y
  `test.server.deps.inline` (Vitest transforma el kit en lugar de cargarlo con Node).
- Cada `tsconfig` de la app lleva `preserveSymlinks` y `allowImportingTsExtensions`: el kit
  importa sus módulos con la extensión `.ts`, para que Node (22.18 o posterior, que quita
  los tipos solo) pueda cargar `@minigestor/web-kit/vite` y `/pwa` cuando Vite lee
  `vite.config.ts`, porque esas importaciones no las empaqueta Vite.

Un `tsc` previo que publicara JavaScript y declaraciones habría añadido un paso a cada build
y a la imagen sin resolver nada de lo anterior: el enlace y las dependencias compartidas son
el mismo problema con o sin `dist`.

## Contratos y tipos

```
API de la app (FastAPI) ──app.openapi()──► apps/<app>/web/openapi/<app>.json ──generate-types──► apps/<app>/web/src/api/generated/<app>.ts
                       tools/export_openapi.py                                 npm run contracts:types
```

- El servicio registra su contrato en [`tools/export_openapi.py`](../../tools/export_openapi.py)
  con la ruta junto a su interfaz. El del gestor vive junto a su cliente de Python
  ([`apps/client/openapi/engine.json`](../client/openapi/engine.json)): ninguna interfaz
  consume el gestor directamente.
- `npm run contracts` (en la interfaz) exporta todos los contratos y regenera sus tipos. El
  generador usa el `openapi-typescript` del kit, así que antes hace falta
  `npm --prefix apps/web-kit ci`.
- La CI vuelve a exportar y a generar y falla si algo cambió respecto de lo versionado.

`/health` se consulta con `createHealthClient`, que tiene su propio tipo: la salud de un
servicio se puede mostrar antes de que publique su contrato.

## Tema y CSP

`webKit()` incrusta en el `<head>` un guion que aplica el tema guardado antes de pintar, con
la misma función que después usa `useThemePreference`: la página no parpadea en el otro
tema. Cada app define sus colores en CSS a partir de `data-theme` (`light` o `dark`) en
`<html>`.

Como es un guion en línea, una `Content-Security-Policy` sin `'unsafe-inline'` tiene que
permitir su hash. El texto exacto depende de cómo se cargó el kit al construir (Node quita
los tipos dejando espacios), así que el hash se calcula sobre el `index.html` construido, con
`inlineScriptHash(script)`, o con `themeBootScriptHash()` en el mismo proceso del build.

## Imagen y nginx

Todas las interfaces se sirven con la misma imagen, [`docker/Dockerfile`](docker/Dockerfile),
construida con `apps/` como contexto y la carpeta de la interfaz en `APP_DIR`:

```bash
docker build --file apps/web-kit/docker/Dockerfile --build-arg APP_DIR=shop/web apps
```

Compila la interfaz con el kit y la sirve con nginx sin privilegios
([`docker/nginx/`](docker/nginx/)). Una sola configuración para las cuatro apps; lo que cambia
de una a otra son variables que pone su `compose.yaml`:

| Variable | Para qué | Por omisión |
|---|---|---|
| `API_UPSTREAM` | la API de la app: el único destino de `/api/` (y del WebSocket) | `http://api:8000` |
| `API_TIMEOUT` | plazo de una respuesta de la API | `60s` |
| `API_KEEPALIVE_CONNECTIONS` | conexiones inactivas con la API que cada proceso de nginx guarda para reutilizar | `32` |
| `API_KEEPALIVE_TIMEOUT` | cuánto espera una de ellas antes de cerrarse; menos que el keep-alive de uvicorn (5 s) | `4s` |
| `API_MAX_BODY_SIZE` | lo más que se puede subir en una petición (`413` si pasa) | `1m` |
| `API_RATE_LIMIT`, `API_RATE_BURST` | peticiones a `/api/` por IP y su ráfaga (`429` si pasa) | `20r/s`, `40` |
| `WEB_PERMISSIONS_POLICY` | `Permissions-Policy`: la cámara, el micrófono o la ubicación, solo en la app que los usa | todo apagado |
| `WEB_IMG_SOURCES` | orígenes extra del `img-src` de la CSP, separados por espacios: las teselas de un mapa | ninguno |
| `WEB_PORT`, `WEB_TLS_PORT` | puertos HTTP y HTTPS dentro del contenedor | `8080`, `8443` |
| `TLS_CERTIFICATE`, `TLS_CERTIFICATE_KEY` | certificado y clave montados; sin ellos, solo HTTP | `/etc/nginx/certs/{cert,key}.pem` |
| `HSTS_MAX_AGE` | `Strict-Transport-Security`, solo en las respuestas por HTTPS | un año |
| `DNS_TTL` | cada cuánto vuelve a resolver la API (réplicas nuevas con `--scale`) | `10s` |

- **`/api/`** se reenvía sin el prefijo y tal como llegó (sin decodificar) a una variable, así
  que nginx resuelve el nombre con el DNS de Docker en cada petición y reparte entre todas
  las réplicas de la API. Pasa el `X-Request-ID` que trae la petición (lo pone el kit) o uno
  nuevo, y su log JSON (`"logger": "nginx"`) dice ese id y la réplica que respondió
  (`upstream`).
- **CSP** sin `'unsafe-inline'`: al construir, [`scripts/csp-script-hashes.ts`](scripts/csp-script-hashes.ts)
  mide el hash de cada guion en línea del `index.html` construido (el del tema) y nginx lo pone
  en `script-src`. Además `X-Content-Type-Options`, `Referrer-Policy`,
  `Cross-Origin-Opener-Policy`, `frame-ancestors 'none'` y gzip.
- **HTTPS**: si hay un certificado montado, `25-tls.sh` añade al arrancar el mismo sitio por
  `WEB_TLS_PORT`. El certificado lo genera `deploy/certs.sh` (ver el README raíz).
- El sistema de archivos puede ser de solo lectura: nginx escribe solo en `/tmp` y en
  `/etc/nginx/conf.d`, que el compose monta como `tmpfs`.

## Crear la interfaz de una aplicación

La plantilla vive en [`template/`](template/), a la misma profundidad que
`apps/<app>/web`, así que sus rutas relativas (`../../web-kit`, `../../..`) sirven tal cual
al copiarla.

```bash
cp -R apps/web-kit/template apps/shop/web
rm -rf apps/shop/web/node_modules apps/shop/web/dist
```

1. En `package.json`, el nombre (`minigestor-shop-web`); después `npm --prefix apps/shop/web
   install` reescribe el `package-lock.json` con la nueva ruta del kit.
2. En `src/identity.ts`, el id, el nombre y el prefijo del API si no es `/api`.
3. Nada para la imagen: todas las interfaces usan la de [`docker/`](docker/) (ver *Imagen y
   nginx*) con `APP_DIR=shop/web`, y el `compose.yaml` de la app pone su puerto y su API.
4. Registrar el contrato del servicio en `tools/export_openapi.py` con la ruta
   `apps/shop/web/openapi/shop.json` y correr `npm --prefix apps/shop/web run contracts`.
5. Reemplazar `src/styles.css`, las páginas y este marcador por la interfaz que define el
   `DESIGN.md` de la app.

La CI recorre la plantilla y cada `apps/*/web` con `package.json`: una interfaz nueva queda
probada sin tocar el flujo de trabajo.

## Comandos

```bash
npm --prefix apps/web-kit ci
npm --prefix apps/web-kit run typecheck    # tipos, en modo estricto
npm --prefix apps/web-kit test             # Vitest

npm --prefix apps/web-kit/template ci
npm --prefix apps/web-kit/template run build
npm --prefix apps/web-kit/template test
npm --prefix apps/web-kit/template run e2e # después de build
docker build --file apps/web-kit/docker/Dockerfile --build-arg APP_DIR=web-kit/template apps
```

Node 22.18 o posterior; la CI y las imágenes usan 22.23.4.
