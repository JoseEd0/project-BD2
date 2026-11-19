import { DEFAULT_API_PREFIX } from "@minigestor/web-kit/pwa";

/** Quién es la app y bajo qué ruta le reenvía nginx su API. Lo leen la interfaz y `vite.config.ts`. */
export const APP = {
  id: "shop",
  name: "Cajón",
  shortName: "Cajón",
  tagline: "instrumentos en Lima",
  description: "Tienda de instrumentos en Lima que explica cada recomendación y muestra dónde hay stock cerca.",
  apiPrefix: DEFAULT_API_PREFIX,
} as const;
