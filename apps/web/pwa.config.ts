import type { VitePWAOptions } from "vite-plugin-pwa";

import { SERVICE_PATH_PREFIXES } from "./src/services/catalog";
import { servicePathPattern, serviceUrlPattern } from "./src/services/serviceRequests";

/** Los iconos que `npm run icons` genera desde los SVG de `public/icons/`. */
export const MANIFEST_ICONS = [
  { src: "icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
  { src: "icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
  { src: "icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
] as const;

// Los mismos valores que `--ink` y `--ground` del tema claro en `src/styles/tokens.css`:
// el manifiesto no puede leer CSS, y el sistema los pinta antes de que cargue la página.
const THEME_COLOR = "#1d1a3a";
const BACKGROUND_COLOR = "#f1f0f7";

/** Solo el shell: lo que produce el build. Nunca una respuesta de un servicio. */
export const SHELL_GLOB_PATTERNS = ["**/*.{html,js,css,svg,png,woff2}"];

export const pwaOptions: Partial<VitePWAOptions> = {
  registerType: "prompt",
  injectRegister: false,
  manifest: {
    id: "/",
    name: "Aplicaciones del Minigestor",
    short_name: "Minigestor",
    description: "Cuatro aplicaciones de IA sobre un gestor de base de datos hecho desde cero.",
    lang: "es",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    theme_color: THEME_COLOR,
    background_color: BACKGROUND_COLOR,
    icons: [...MANIFEST_ICONS],
  },
  workbox: {
    globPatterns: SHELL_GLOB_PATTERNS,
    navigateFallback: "index.html",
    navigateFallbackDenylist: [servicePathPattern(SERVICE_PATH_PREFIXES)],
    cleanupOutdatedCaches: true,
    // Explícito aunque una ruta sin manejador ya iría a la red: así queda escrito, y probado,
    // que ninguna respuesta de un servicio pasa por una caché.
    runtimeCaching: [{ urlPattern: serviceUrlPattern(SERVICE_PATH_PREFIXES), handler: "NetworkOnly" }],
  },
};
