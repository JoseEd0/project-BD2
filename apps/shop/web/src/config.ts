import { APP } from "./identity";

/**
 * Dónde está el API, desde las variables de entorno de Vite (ver `.env.example`). Vacía o
 * ausente, `VITE_API_BASE_URL` es el prefijo del API en el mismo origen de la interfaz.
 */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || APP.apiPrefix;

/** Las teselas del mapa; su origen tiene que estar en el `img-src` de la CSP (`WEB_IMG_SOURCES`). */
export const MAP_TILES_URL: string = import.meta.env.VITE_MAP_TILES_URL || "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const MAP_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

/** La Plaza Mayor de Lima: el punto de partida cuando el navegador no da la ubicación. */
export const DEFAULT_LOCATION = { lat: -12.0453, lon: -77.0311 } as const;
/** El radio de «en stock cerca», el mismo que el API usa por omisión (`SHOP_NEARBY_RADIUS_METERS`). */
export const NEARBY_RADIUS_METERS = 5000;

export const SEARCH_RESULTS = 24;
/** Lo que muestra la vista previa bajo el buscador y cuánto se espera a que dejes de escribir. */
export const PREVIEW_RESULTS = 6;
export const PREVIEW_DEBOUNCE_MILLISECONDS = 180;
export const PREVIEW_MIN_CHARACTERS = 2;
/** Recomendaciones que se piden: la fila fundida y, de ellas, las de cada señal. */
export const RECOMMENDATIONS = 24;
export const STORES_SHOWN = 8;
export const PURCHASES_SHOWN = 8;

/** Lado mayor de la foto que se sube para buscar: CLIP ve 224 px, así que 1024 sobran. */
export const UPLOAD_MAX_SIDE = 1024;
export const UPLOAD_JPEG_QUALITY = 0.9;
/** Lo más que se acepta elegir; el API admite 8 MiB después de reducirla. */
export const UPLOAD_MAX_SOURCE_BYTES = 25 * 1024 * 1024;
