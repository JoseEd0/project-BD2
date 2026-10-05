/**
 * Direcciones externas que usa la interfaz. Vienen de las variables de entorno de Vite
 * (ver `.env.example`); los valores por omisión son los del desarrollo local.
 */

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** Plantilla de teselas del mapa, en el formato `{z}/{x}/{y}` de Leaflet. */
export const MAP_TILES_URL =
  import.meta.env.VITE_MAP_TILES_URL ?? "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

export const MAP_ATTRIBUTION =
  import.meta.env.VITE_MAP_ATTRIBUTION ??
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
