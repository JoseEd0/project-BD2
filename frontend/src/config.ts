/**
 * Direcciones externas que usa la interfaz. Vienen de las variables de entorno de Vite
 * (ver `frontend/.env.example`).
 */

/** URL del API del motor. Sin ella no hay a quién preguntar: ver `apiUrl` en `api.ts`. */
export const API_URL: string | undefined = import.meta.env.VITE_API_URL;

export const MISSING_API_URL =
  "Falta VITE_API_URL: copia frontend/.env.example a frontend/.env y vuelve a arrancar la interfaz";

/** Plantilla de teselas del mapa, en el formato `{z}/{x}/{y}` de Leaflet. Opcional: por
 * omisión, las de OpenStreetMap, que son públicas y no dependen de este proyecto. */
export const MAP_TILES_URL =
  import.meta.env.VITE_MAP_TILES_URL ?? "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

export const MAP_ATTRIBUTION =
  import.meta.env.VITE_MAP_ATTRIBUTION ??
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
