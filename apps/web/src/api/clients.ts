import createClient, { type Client } from "openapi-fetch";

import type { paths as EnginePaths } from "./generated/engine";

/**
 * La ruta de salud que comparten todos los servicios de la plataforma. Se toma del contrato
 * del gestor, que es el primero: un servicio que todavía no publica su contrato se consulta
 * con este mismo tipo.
 */
export type HealthPaths = Pick<EnginePaths, "/health">;

export type HealthClient = Client<HealthPaths>;

export interface ClientOptions {
  readonly baseUrl: string;
  /** Para los tests; por omisión, el `fetch` del navegador. */
  readonly fetch?: (request: Request) => Promise<Response>;
}

export function createHealthClient(options: ClientOptions): HealthClient {
  return createClient<HealthPaths>(options);
}
