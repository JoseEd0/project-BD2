import { APP } from "./identity";

/**
 * Dónde está el API, desde las variables de entorno de Vite (ver `.env.example`). Vacía o
 * ausente, `VITE_API_BASE_URL` es el prefijo del API en el mismo origen de la interfaz.
 */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || APP.apiPrefix;
