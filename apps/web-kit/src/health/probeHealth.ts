import type { Client } from "openapi-fetch";

import { createServiceClient, type ServiceClientOptions } from "../client/serviceClient.ts";
import { SERVER_TIMING_HEADER } from "../timing/serverTiming.ts";
import { healthFromFailure, healthFromResponse, unavailable, type ServiceHealth } from "./health.ts";

/**
 * La ruta de salud que comparten todos los servicios de la plataforma, escrita como la
 * generaría `openapi-typescript`: así se consulta también un servicio que todavía no publica
 * su contrato.
 */
export interface HealthPaths {
  "/health": {
    parameters: { query?: never; header?: never; path?: never; cookie?: never };
    get: {
      parameters: { query?: never; header?: never; path?: never; cookie?: never };
      requestBody?: never;
      responses: {
        200: {
          headers: { [name: string]: unknown };
          content: { "application/json": { status: string; version?: string } };
        };
      };
    };
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
}

export type HealthClient = Client<HealthPaths>;

export function createHealthClient(options: ServiceClientOptions): HealthClient {
  return createServiceClient<HealthPaths>(options);
}

const TIMED_OUT = Symbol("timed out");

/**
 * Pregunta a `/health` y traduce cualquier resultado a un `ServiceHealth`. Solo lanza si
 * quien llama cancela (`cancellation`), para que TanStack Query descarte la consulta en
 * vez de pintar el servicio como caído.
 *
 * El plazo y la cancelación ganan la carrera a la petición con un temporizador y un
 * `AbortController` propios, y no con `AbortSignal.timeout` ni `AbortSignal.any`: con jsdom
 * y Node 22, la señal de un entorno no siempre aborta el `fetch` del otro, y la consulta
 * quedaba colgada en vez de agotar su plazo.
 */
export async function probeHealth(
  client: HealthClient,
  cancellation: AbortSignal,
  timeoutMs: number,
): Promise<ServiceHealth> {
  cancellation.throwIfAborted();
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel = (): void => undefined;
  const interruption = new Promise<typeof TIMED_OUT>((resolve, reject) => {
    timer = setTimeout(() => resolve(TIMED_OUT), timeoutMs);
    cancel = () => reject(cancellation.reason);
  });
  cancellation.addEventListener("abort", cancel, { once: true });
  try {
    const outcome = await Promise.race([client.GET("/health", { signal: controller.signal }), interruption]);
    if (outcome === TIMED_OUT) {
      return unavailable("timeout");
    }
    const { data, response } = outcome;
    return healthFromResponse(response.status, data ?? null, response.headers.get(SERVER_TIMING_HEADER));
  } catch (error) {
    if (cancellation.aborted) {
      throw error;
    }
    return healthFromFailure(error);
  } finally {
    clearTimeout(timer);
    cancellation.removeEventListener("abort", cancel);
    controller.abort();
  }
}
