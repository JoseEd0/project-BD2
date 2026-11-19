import { SERVER_TIMING_HEADER } from "@minigestor/web-kit";
import { useQueries, useQuery, type UseQueryResult } from "@tanstack/react-query";

import { papersApi } from "../api/client";
import { toRequestFilters, type Facets, type ResearchFilters, type SearchMode, type SearchResponse } from "../api/types";

/** Los fragmentos que muestra cada lista del panel de recuperación. */
export const RETRIEVAL_RESULTS = 10;
export const RETRIEVAL_MODES: readonly SearchMode[] = ["bm25", "vector", "hybrid"];

export interface RetrievalResult {
  readonly response: SearchResponse;
  readonly serverTiming: string | null;
}

export class RetrievalError extends Error {
  constructor(readonly status: number, readonly kind: string | null) {
    super(kind ?? `HTTP ${status}`);
    this.name = "RetrievalError";
  }
}

async function search(question: string, mode: SearchMode, filters: ResearchFilters, signal: AbortSignal): Promise<RetrievalResult> {
  const { data, error, response } = await papersApi.POST("/search", {
    body: { query: question, mode, k: RETRIEVAL_RESULTS, filters: toRequestFilters(filters) ?? null },
    signal,
  });
  if (data === undefined) {
    const kind = typeof error === "object" && error !== null && "kind" in error ? String(error.kind) : null;
    throw new RetrievalError(response.status, kind);
  }
  return { response: data, serverTiming: response.headers.get(SERVER_TIMING_HEADER) };
}

/** Las tres búsquedas de una pregunta: BM25, embeddings y la fusión, con sus filtros. */
export function useRetrieval(question: string, filters: ResearchFilters): UseQueryResult<RetrievalResult>[] {
  return useQueries({
    queries: RETRIEVAL_MODES.map((mode) => ({
      queryKey: ["search", question, mode, filters],
      queryFn: ({ signal }: { signal: AbortSignal }) => search(question, mode, filters, signal),
      staleTime: Infinity,
      retry: false,
    })),
  });
}

/** Los años y las categorías del corpus, para los filtros. */
export function useFacets(): UseQueryResult<Facets> {
  return useQuery({
    queryKey: ["facets"],
    queryFn: async ({ signal }) => {
      const { data, response } = await papersApi.GET("/facets", { signal });
      if (data === undefined) {
        throw new RetrievalError(response.status, null);
      }
      return data;
    },
    staleTime: Infinity,
  });
}

export function usePage(paperId: string | null, page: number | null) {
  return useQuery({
    queryKey: ["page", paperId, page],
    enabled: paperId !== null && page !== null,
    queryFn: async ({ signal }) => {
      const { data, response } = await papersApi.GET("/papers/{paper_id}/pages/{page}", {
        params: { path: { paper_id: paperId ?? "", page: page ?? 1 } },
        signal,
      });
      if (data === undefined) {
        throw new RetrievalError(response.status, null);
      }
      return data;
    },
    staleTime: Infinity,
    retry: false,
  });
}
