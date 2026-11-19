import { DEFAULT_API_PREFIX } from "@minigestor/web-kit/pwa";

/** Quién es la app y bajo qué ruta le reenvía nginx su API. Lo leen la interfaz y `vite.config.ts`. */
export const APP = {
  id: "copyright",
  name: "Songcheck",
  shortName: "Songcheck",
  tagline: "copyright en audio",
  description: "Sube una canción y escucha, tramo a tramo, qué partes coinciden con el catálogo y con qué original.",
  apiPrefix: DEFAULT_API_PREFIX,
} as const;
