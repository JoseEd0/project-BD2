import { DEFAULT_API_PREFIX } from "@minigestor/web-kit/pwa";

/** Quién es la app y bajo qué ruta le reenvía nginx su API. Lo leen la interfaz y `vite.config.ts`. */
export const APP = {
  id: "faces",
  name: "Rostros",
  shortName: "Rostros",
  description: "Regístrate con tu cara en tres tomas y mira cómo te reconoce la cámara en vivo.",
  apiPrefix: DEFAULT_API_PREFIX,
} as const;
