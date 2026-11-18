import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { createHealthClient } from "../api/clients";
import { API_BASE_URL } from "../config";
import { serviceBaseUrl, type PortalService } from "./catalog";
import type { ServiceHealth } from "./health";
import { probeHealth } from "./probeHealth";

/** Cada cuánto se vuelve a preguntar: lo bastante seguido para ver un servicio que acaba de
 * arrancar sin recargar, lo bastante espaciado para no ser tráfico. */
const HEALTH_REFRESH_INTERVAL_MS = 15_000;
const HEALTH_TIMEOUT_MS = 4_000;

function healthQueryKey(service: PortalService): readonly unknown[] {
  return ["health", service.id];
}

/** La salud del servicio, consultada a través del gateway y refrescada sola. */
export function useServiceHealth(service: PortalService): UseQueryResult<ServiceHealth> {
  return useQuery({
    queryKey: healthQueryKey(service),
    queryFn: ({ signal }) =>
      probeHealth(
        createHealthClient({ baseUrl: serviceBaseUrl(API_BASE_URL, service) }),
        signal,
        HEALTH_TIMEOUT_MS,
      ),
    refetchInterval: HEALTH_REFRESH_INTERVAL_MS,
    // El sondeo ya convierte cada fallo en «no disponible»: reintentar solo lo retrasa.
    retry: false,
  });
}
