import type { Plugin } from "vite";

const KIT_PACKAGE = "@minigestor/web-kit";

/** Lo que el kit importa y la app también: tiene que haber una sola copia en el bundle. */
export const SHARED_DEPENDENCIES: readonly string[] = ["react", "react-dom", "openapi-fetch"];

/**
 * Cómo resuelve Vite (y Vitest) un kit que llega como enlace a sus fuentes TypeScript
 * (`file:../../web-kit`):
 *
 * - `preserveSymlinks`: las importaciones del kit se buscan en el `node_modules` de la app,
 *   como las de un paquete publicado; así compila aunque el kit no tenga las suyas (la
 *   imagen de Docker solo instala las de la app).
 * - `dedupe`: si el kit sí tiene su `node_modules` (para sus tests), React y openapi-fetch
 *   se toman igual de la app, o habría dos React y los hooks del kit fallarían.
 * - `server.deps.inline`: Vitest transforma el kit en vez de pedirle a Node que lo cargue.
 */
export function kitResolution(): Plugin {
  return {
    name: "minigestor-web-kit-resolution",
    config: () => ({
      resolve: { preserveSymlinks: true, dedupe: [...SHARED_DEPENDENCIES] },
      test: { server: { deps: { inline: [KIT_PACKAGE] } } },
    }),
  };
}
