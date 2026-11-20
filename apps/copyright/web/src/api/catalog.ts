import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { problemOf, type Problem } from "../analysis/problem";
import { copyrightClient, songAudioUrl } from "./client";
import type { CatalogSummary, Song } from "./types";

const CATALOG_PAGE_SIZE = 40;
const CATALOG_REFRESH_MS = 15_000;

/** Una lectura del catálogo que el API rechazó, con el motivo listo para mostrarse. */
export class CatalogProblem extends Error {
  constructor(readonly problem: Problem) {
    super(problem.title);
    this.name = "CatalogProblem";
  }
}

function rejected(response: Response, error: unknown): CatalogProblem {
  return new CatalogProblem(problemOf(response.status, error));
}

/** El tamaño del catálogo y si ya se puede buscar en él; se refresca mientras se carga. */
export function useCatalogSummary(): UseQueryResult<CatalogSummary> {
  return useQuery<CatalogSummary>({
    queryKey: ["copyright", "catalog"],
    refetchInterval: (query) => (query.state.data?.segments_indexed === true ? false : CATALOG_REFRESH_MS),
    queryFn: async ({ signal }) => {
      const { data, error, response } = await copyrightClient.GET("/catalog", { signal });
      if (data === undefined) {
        throw rejected(response, error);
      }
      return data;
    },
  });
}

/** Una página de canciones: las que mejor responden a `search` o, sin texto, las primeras. */
export function useSongs(search: string): UseQueryResult<readonly Song[]> {
  const query = search.trim();
  return useQuery({
    queryKey: ["copyright", "songs", query],
    placeholderData: (previous) => previous,
    queryFn: async ({ signal }) => {
      const { data, error, response } = await copyrightClient.GET("/songs", {
        params: { query: { limit: CATALOG_PAGE_SIZE, ...(query === "" ? {} : { query }) } },
        signal,
      });
      if (data === undefined) {
        throw rejected(response, error);
      }
      return data.songs;
    },
  });
}

/** El audio de una canción del catálogo como archivo, para analizarlo igual que una subida. */
export async function songFile(song: Song, signal?: AbortSignal): Promise<File> {
  const response = await fetch(songAudioUrl(song.id), signal === undefined ? {} : { signal });
  if (!response.ok) {
    throw new CatalogProblem(problemOf(response.status, null));
  }
  const blob = await response.blob();
  return new File([blob], `${song.title} — ${song.artist}.mp3`, { type: blob.type || "audio/mpeg" });
}
