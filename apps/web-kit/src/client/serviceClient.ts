import createClient, { type Client, type Middleware } from "openapi-fetch";

import { DEFAULT_API_PREFIX } from "../apiPrefix.ts";

/** Lo comparten el navegador, el API de cada app y el gestor: une las tres líneas de log. */
export const REQUEST_ID_HEADER = "X-Request-ID";

export interface ServiceClientOptions {
  /** Absoluta (`https://shop.test/api`) o relativa al origen de la página (`/api`). */
  readonly baseUrl: string;
  /** Para los tests; por omisión, el `fetch` del navegador. */
  readonly fetch?: (request: Request) => Promise<Response>;
}

/** La base de un API bajo `origin`, sin barras dobles entre las dos partes. */
export function serviceBaseUrl(origin: string, prefix: string = DEFAULT_API_PREFIX): string {
  return origin.replace(/\/+$/, "") + prefix;
}

/** Una petición que ya trae su id (un reintento, por ejemplo) lo conserva. */
const stampRequestId: Middleware = {
  onRequest({ request }) {
    if (!request.headers.has(REQUEST_ID_HEADER)) {
      request.headers.set(REQUEST_ID_HEADER, crypto.randomUUID());
    }
    return request;
  },
};

/**
 * Un cliente tipado con los `paths` que `openapi-typescript` genera del contrato del
 * servicio. Cada petición sale con su `X-Request-ID`, para seguirla en los logs del API de
 * la app y del gestor.
 */
export function createServiceClient<Paths extends object>(options: ServiceClientOptions): Client<Paths> {
  const client = createClient<Paths>(options);
  client.use(stampRequestId);
  return client;
}
