import { createHealthClient, healthQuery, type ServiceHealth } from "@minigestor/web-kit";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { APP } from "../identity";
import { API_BASE_URL } from "../config";

/** La salud del API de esta app, refrescada sola. */
export function useServiceHealth(): UseQueryResult<ServiceHealth> {
  return useQuery(healthQuery(APP.id, createHealthClient({ baseUrl: API_BASE_URL })));
}
