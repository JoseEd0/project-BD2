import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { copyrightClient } from "./client";
import type { SongDetail } from "./types";

/** Una canción del catálogo (duración y género), para dibujar su pentagrama. */
export function useSong(songId: number | null): UseQueryResult<SongDetail> {
  return useQuery({
    queryKey: ["copyright", "song", songId],
    enabled: songId !== null,
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      const { data, error } = await copyrightClient.GET("/songs/{song_id}", {
        params: { path: { song_id: songId ?? 0 } },
        signal,
      });
      if (data === undefined) {
        throw new Error(`no se pudo leer la canción ${songId}: ${JSON.stringify(error)}`);
      }
      return data;
    },
  });
}
