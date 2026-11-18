import { DEFAULT_API_PREFIX } from "@minigestor/web-kit/pwa";

/**
 * Lo que se cambia al copiar la plantilla a `apps/<app>/web`: quién es la app y bajo qué
 * ruta le reenvía nginx su API. Lo leen la interfaz y `vite.config.ts`.
 */
export const APP = {
  id: "template",
  name: "Aplicación de ejemplo",
  shortName: "Ejemplo",
  description: "Plantilla de las interfaces de la Parte 5 sobre el kit web del minigestor.",
  apiPrefix: DEFAULT_API_PREFIX,
} as const;
