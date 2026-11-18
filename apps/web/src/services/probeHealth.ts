import type { HealthClient } from "../api/clients";
import { SERVER_TIMING_HEADER } from "../timing/serverTiming";
import { healthFromFailure, healthFromResponse, type ServiceHealth } from "./health";

/**
 * Pregunta a `/health` y traduce cualquier resultado a un `ServiceHealth`. Solo lanza si
 * quien llama cancela (`cancellation`), para que TanStack Query descarte la consulta en
 * vez de pintar el servicio como caído.
 */
export async function probeHealth(
  client: HealthClient,
  cancellation: AbortSignal,
  timeoutMs: number,
): Promise<ServiceHealth> {
  const signal = AbortSignal.any([cancellation, AbortSignal.timeout(timeoutMs)]);
  try {
    const { data, response } = await client.GET("/health", { signal });
    return healthFromResponse(response.status, data ?? null, response.headers.get(SERVER_TIMING_HEADER));
  } catch (error) {
    if (cancellation.aborted) {
      throw error;
    }
    return healthFromFailure(error);
  }
}
