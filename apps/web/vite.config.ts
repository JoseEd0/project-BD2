import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import { loadEnv, type Plugin, type ProxyOptions } from "vite";
import { defineConfig } from "vitest/config";

import { pwaOptions } from "./pwa.config";
import { APPS_MOUNT_ROOT, ENGINE_MOUNT_PATH } from "./src/services/catalog";
import { themeBootScript } from "./src/theme/themePreference";

/** Variables solo del servidor de desarrollo: sin el prefijo `VITE_`, nunca llegan al bundle. */
const DEV_ENV_PREFIX = "PORTAL_DEV_";
const TEST_API_BASE_URL = "http://portal.test";

/** El tema se fija antes de pintar con la misma función que lo cambia después. */
function themeBoot(): Plugin {
  return {
    name: "portal-theme-boot",
    transformIndexHtml: () => [{ tag: "script", children: themeBootScript(), injectTo: "head" }],
  };
}

/**
 * En desarrollo, `/api` va al gestor sin el prefijo (como lo reenvía nginx) y `/apps` a
 * donde estén los servicios. Sin la variable, la ruta no se reenvía y el servicio aparece
 * como no disponible, que es la verdad.
 */
function devProxy(env: Record<string, string>): Record<string, ProxyOptions> {
  const proxy: Record<string, ProxyOptions> = {};
  const engine = env.PORTAL_DEV_ENGINE_URL;
  const apps = env.PORTAL_DEV_APPS_URL;
  if (engine) {
    proxy[ENGINE_MOUNT_PATH] = {
      target: engine,
      changeOrigin: true,
      rewrite: (path) => path.slice(ENGINE_MOUNT_PATH.length),
    };
  }
  if (apps) {
    proxy[APPS_MOUNT_ROOT] = { target: apps, changeOrigin: true };
  }
  return proxy;
}

export default defineConfig(({ mode }) => {
  const proxy = devProxy(loadEnv(mode, process.cwd(), DEV_ENV_PREFIX));
  return {
    plugins: [react(), themeBoot(), VitePWA(pwaOptions)],
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
