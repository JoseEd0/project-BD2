/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** URL del API del motor; se define en `.env` (ver `.env.example`). */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
