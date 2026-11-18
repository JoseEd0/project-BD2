/**
 * Dónde viven los servicios, desde las variables de entorno de Vite (ver `.env.example`).
 *
 * Vacía o ausente, `VITE_API_BASE_URL` es el mismo origen del portal: el gateway sirve el
 * portal en `/`, el gestor en `/api` y cada aplicación en `/apps/<servicio>`.
 */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "";
