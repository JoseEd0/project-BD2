/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/react" />

interface ImportMetaEnv {
  /** Dónde está el API de la app; vacía, su prefijo en el mismo origen. */
  readonly VITE_API_BASE_URL?: string;
  /** La plantilla de las teselas del mapa (`{z}/{x}/{y}`); por omisión, OpenStreetMap. */
  readonly VITE_MAP_TILES_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
