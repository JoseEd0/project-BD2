import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { problemOf, type Problem } from "../analysis/problem";
import { copyrightClient, songAudioUrl } from "./client";
import { API_BASE_URL } from "../config";
import type { CatalogSummary, DemoClip, Song } from "./types";

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

async function remoteFile(url: string, name: string, fallbackType: string, signal: AbortSignal): Promise<File> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new CatalogProblem(problemOf(response.status, null));
  }
  const blob = await response.blob();
  return new File([blob], name, { type: blob.type || fallbackType });
}

/** El audio de una canción del catálogo como archivo, para analizarlo igual que una subida. */
export function songFile(song: Song, signal: AbortSignal): Promise<File> {
  return remoteFile(songAudioUrl(song.id), `${song.title} — ${song.artist}.mp3`, "audio/mpeg", signal);
}

export function demoClipAudioUrl(clip: DemoClip): string {
  return `${API_BASE_URL}/demo-clips/${encodeURIComponent(clip.id)}/audio`;
}

/** Un audio de prueba como archivo, para analizarlo igual que una subida. */
export function demoClipFile(clip: DemoClip, signal: AbortSignal): Promise<File> {
  return remoteFile(demoClipAudioUrl(clip), `${clip.title}.wav`, "audio/wav", signal);
}

/** Los audios de prueba: clips con música del catálogo y uno sin ella. */
export function useDemoClips(): UseQueryResult<readonly DemoClip[]> {
  return useQuery({
    queryKey: ["copyright", "demo-clips"],
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      const { data, error, response } = await copyrightClient.GET("/demo-clips", { signal });
      if (data === undefined) {
        throw rejected(response, error);
      }
      return data;
    },
  });
}
