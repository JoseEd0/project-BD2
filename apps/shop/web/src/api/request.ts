import { SERVER_TIMING_HEADER } from "@minigestor/web-kit";

import { asProblem, problemOf } from "./problem";
import type { CacheStatus, Measured } from "./types";

export const CACHE_HEADER = "X-Cache";
const CACHE_STATUSES: readonly CacheStatus[] = ["hit", "miss", "bypass"];

interface FetchResult<T> {
  readonly data?: T;
  readonly error?: unknown;
  readonly response: Response;
}

function cacheStatusOf(headers: Headers): CacheStatus | null {
  const value = headers.get(CACHE_HEADER);
  return CACHE_STATUSES.find((status) => status === value) ?? null;
}

/**
 * El cuerpo de una llamada del cliente tipado con lo que traen sus cabeceras. Una respuesta
 * de error lanza `ApiProblem` con el motivo del API; sin respuesta, «la tienda no responde».
 */
export async function measured<T>(call: () => Promise<FetchResult<T>>): Promise<Measured<T>> {
  let result: FetchResult<T>;
  try {
    result = await call();
  } catch (error) {
    throw asProblem(error);
  }
  const { data, error, response } = result;
  if (!response.ok || data === undefined) {
    throw problemOf(response.status, error);
  }
  return { data, serverTiming: response.headers.get(SERVER_TIMING_HEADER), cache: cacheStatusOf(response.headers) };
}

/** Solo el cuerpo, para las lecturas cuyas cabeceras no se muestran. */
export async function body<T>(call: () => Promise<FetchResult<T>>): Promise<T> {
  return (await measured(call)).data;
}
