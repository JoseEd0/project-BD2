import { createServiceClient } from "@minigestor/web-kit";

import { API_BASE_URL } from "../config";
import type { paths } from "./generated/shop";

export const shopClient = createServiceClient<paths>({ baseUrl: API_BASE_URL });

/** La foto de un producto, que el API sirve desde el dataset con caché del navegador. */
export function productPhotoUrl(productId: string): string {
  return `${API_BASE_URL}/productos/${encodeURIComponent(productId)}/foto`;
}
