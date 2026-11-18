import type { ManifestOptions, VitePWAOptions } from "vite-plugin-pwa";

import { DEFAULT_API_PREFIX } from "../apiPrefix.ts";
import { apiPathPattern, apiUrlPattern } from "./apiRequests.ts";

/** Solo el shell: lo que produce el build. Nunca una respuesta de un API. */
export const SHELL_GLOB_PATTERNS: readonly string[] = ["**/*.{html,js,css,svg,png,woff2}"];

export interface AppPwaOptions {
  /** Nombre, colores e iconos de la app: su identidad, que el kit no decide. */
  readonly manifest: Partial<ManifestOptions>;
  /** Todo lo que responde el API de la app; por omisión, `/api`. */
  readonly apiPrefixes?: readonly string[];
}

/**
 * Las opciones de `vite-plugin-pwa` de una app. El service worker precarga solo el shell;
 * las rutas del API van siempre a la red (`NetworkOnly`) y quedan fuera del `index.html` de
 * respaldo, así que ninguna respuesta del servicio se guarda ni se sirve vieja. Una versión
 * nueva espera a que el usuario la acepte (`registerType: "prompt"`), y el registro lo hace
 * la app con `virtual:pwa-register/react`.
 */
export function createPwaOptions({ manifest, apiPrefixes = [DEFAULT_API_PREFIX] }: AppPwaOptions): Partial<VitePWAOptions> {
  return {
    registerType: "prompt",
    injectRegister: false,
    manifest,
    workbox: {
      globPatterns: [...SHELL_GLOB_PATTERNS],
      navigateFallback: "index.html",
      navigateFallbackDenylist: [apiPathPattern(apiPrefixes)],
      cleanupOutdatedCaches: true,
      // Explícito aunque una ruta sin manejador ya iría a la red: así queda escrito, y probado,
      // que ninguna respuesta del API pasa por una caché.
      runtimeCaching: [{ urlPattern: apiUrlPattern(apiPrefixes), handler: "NetworkOnly" }],
    },
  };
}
