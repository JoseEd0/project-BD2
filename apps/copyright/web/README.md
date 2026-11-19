# Interfaz de una aplicación

Plantilla de la interfaz de una aplicación de la Parte 5, creada con el kit
([`apps/web-kit`](../../web-kit/README.md)). Al copiarla a `apps/<app>/web`, este README pasa a ser el de
esa interfaz: se reemplaza por lo que la app hace, su `PRODUCT.md` y su `DESIGN.md`.

| Archivo | Qué es |
|---|---|
| `src/identity.ts` | nombre de la app y prefijo de su API (`/api`) |
| `src/api/` | la salud del API y, cuando el servicio publique su contrato, los tipos generados |
| `src/components/`, `src/pages/` | un marcador neutro: estado del servicio, tiempos por etapa y tema |
| `vite.config.ts` | el kit (`webKit()`), la PWA (`createPwaOptions`) y el reenvío de `/api` en desarrollo |
| `tests/`, `e2e/` | Vitest en jsdom y Playwright en Chromium contra el build |

La imagen no es de la interfaz: es la común de todas, [`web-kit/docker/`](../docker/), con
`APP_DIR=<app>/web`.

```bash
npm ci
npm run dev        # http://localhost:5173, /api reenviado a WEB_DEV_API_URL
npm run build      # tipos (tsc -b) y bundle con el service worker
npm test           # Vitest
npm run e2e        # Playwright (después de build; la primera vez, npx playwright install chromium)
```
