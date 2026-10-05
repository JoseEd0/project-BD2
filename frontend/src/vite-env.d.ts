/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** URL del API del motor; se define en `.env` (ver `.env.example`). */
  readonly VITE_API_URL?: string;
  /** Plantilla de teselas del panel de mapa; por omisión, OpenStreetMap. */
  readonly VITE_MAP_TILES_URL?: string;
  /** Texto de atribución que el proveedor de teselas exige mostrar. */
  readonly VITE_MAP_ATTRIBUTION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
