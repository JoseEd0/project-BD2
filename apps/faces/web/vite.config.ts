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

// La cortina de `src/styles.css` (`--curtain`): el manifiesto no puede leer CSS, y el sistema
// pinta este color en la barra y en el arranque, antes de que cargue la página.
const CURTAIN_COLOR = "#0c2b3a";

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
            orientation: "portrait",
            categories: ["photo", "education"],
            theme_color: CURTAIN_COLOR,
            background_color: CURTAIN_COLOR,
            icons: [
              { src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
              { src: "icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
              { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
              { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
            ],
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
