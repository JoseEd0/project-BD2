import { createServiceClient } from "@minigestor/web-kit";

import { API_BASE_URL } from "../config";
import type { paths } from "./generated/shop";

/** `fetch` se busca en cada petición y no al crear el cliente: así los tests lo pueden sustituir. */
export const shopClient = createServiceClient<paths>({ baseUrl: API_BASE_URL, fetch: (request) => globalThis.fetch(request) });

/** La foto de un producto, que el API sirve desde el dataset con caché del navegador. */
export function productPhotoUrl(productId: string): string {
  return `${API_BASE_URL}/productos/${encodeURIComponent(productId)}/foto`;
}
