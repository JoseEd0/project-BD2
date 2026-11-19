import { createServiceClient } from "@minigestor/web-kit";

import { API_BASE_URL } from "../config";
import type { paths } from "./generated/rag";

/**
 * El cliente tipado del API de papers, con un `X-Request-ID` por petición. Busca `fetch` en
 * cada petición y no al crearse, como `streamAnswer`: los dos caminos ven el mismo `fetch`.
 */
export const papersApi = createServiceClient<paths>({
  baseUrl: API_BASE_URL,
  fetch: (request) => globalThis.fetch(request),
});

/** La URL absoluta (respecto del origen) de una ruta del API, como `/papers/{id}/file`. */
export function apiUrl(path: string): string {
  return API_BASE_URL.replace(/\/+$/, "") + path;
}
