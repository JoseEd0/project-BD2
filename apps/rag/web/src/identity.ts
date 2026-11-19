import { DEFAULT_API_PREFIX } from "@minigestor/web-kit/pwa";

/** Quién es la app y bajo qué ruta le reenvía nginx su API. Lo leen la interfaz y `vite.config.ts`. */
export const APP = {
  id: "papers",
  name: "Papers",
  shortName: "Papers",
  description: "Preguntas a papers de bases de datos y recuperación de información, con cada cita abierta en su página del PDF.",
  apiPrefix: DEFAULT_API_PREFIX,
} as const;
