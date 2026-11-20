import react from "@vitejs/plugin-react";
import { createPwaOptions } from "@minigestor/web-kit/pwa";
import { webKit } from "@minigestor/web-kit/vite";
import { VitePWA } from "vite-plugin-pwa";
import { loadEnv, type ProxyOptions } from "vite";
import { defineConfig } from "vitest/config";

import { APP } from "./src/identity";

/** Variables solo del servidor de desarrollo: sin el prefijo `VITE_`, nunca llegan al bundle. */
const DEV_ENV_PREFIX = "WEB_DEV_";
const TEST_API_BASE_URL = "http://app.test/api";

// Los mismos valores que `--floor` y `--tolex` del tema claro en `src/styles/tokens.css`: el
// manifiesto no puede leer CSS, y el sistema los pinta antes de que cargue la página.
const BACKGROUND_COLOR = "#f2f3f1";
const THEME_COLOR = "#131a22";

/** En desarrollo, el API se reenvía sin su prefijo, como lo hace nginx. */
function devProxy(env: Record<string, string>): Record<string, ProxyOptions> {
  const api = env.WEB_DEV_API_URL;
  if (!api) {
    return {};
  }
  return {
    [APP.apiPrefix]: { target: api, changeOrigin: true, ws: true, rewrite: (path) => path.slice(APP.apiPrefix.length) },
  };
}

export default defineConfig(({ mode }) => {
  const proxy = devProxy(loadEnv(mode, process.cwd(), DEV_ENV_PREFIX));
  return {
    plugins: [
      react(),
      webKit(),
      VitePWA(
        createPwaOptions({
          apiPrefixes: [APP.apiPrefix],
          manifest: {
            id: "/",
            name: APP.name,
            short_name: APP.shortName,
            description: APP.description,
            lang: "es",
            start_url: "/",
            scope: "/",
            display: "standalone",
            theme_color: THEME_COLOR,
            background_color: BACKGROUND_COLOR,
            icons: [{ src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
          },
        }),
      ),
    ],
    server: { proxy },
    preview: { proxy },
    test: {
      environment: "jsdom",
      include: ["tests/**/*.test.{ts,tsx}"],
      setupFiles: ["tests/setup.ts"],
      // Un origen absoluto: en Node, `fetch` no acepta URLs relativas.
      env: { VITE_API_BASE_URL: TEST_API_BASE_URL },
    },
  };
});
