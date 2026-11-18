import type { ServiceHealth } from "./health.ts";
import { probeHealth, type HealthClient } from "./probeHealth.ts";

/** Cada cuánto se vuelve a preguntar: lo bastante seguido para ver un servicio que acaba de
 * arrancar sin recargar, lo bastante espaciado para no ser tráfico. */
export const HEALTH_REFRESH_INTERVAL_MS = 15_000;
export const HEALTH_TIMEOUT_MS = 4_000;

export interface HealthQueryOptions {
  readonly queryKey: readonly unknown[];
  readonly queryFn: (context: { signal: AbortSignal }) => Promise<ServiceHealth>;
  readonly refetchInterval: number;
  readonly retry: false;
}

/**
 * Las opciones de `useQuery` para la salud de un servicio, refrescada sola. El sondeo ya
 * convierte cada fallo en «no disponible»: reintentar solo lo retrasaría.
 *
 *     const { data } = useQuery(healthQuery("shop", client));
 */
export function healthQuery(serviceName: string, client: HealthClient): HealthQueryOptions {
  return {
    queryKey: ["health", serviceName],
    queryFn: ({ signal }) => probeHealth(client, signal, HEALTH_TIMEOUT_MS),
    refetchInterval: HEALTH_REFRESH_INTERVAL_MS,
    retry: false,
  };
}
