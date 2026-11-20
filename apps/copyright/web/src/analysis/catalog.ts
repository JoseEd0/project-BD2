import type { CatalogSummary, Detection } from "../api/types";

export type CatalogState = "empty" | "indexing" | "ready";

/** Si el catálogo ya responde búsquedas: sin canciones, sin el HNSW de sus tramos o listo. */
export function catalogStateOf(summary: CatalogSummary): CatalogState {
  if (summary.songs === 0 || summary.segments === 0) {
    return "empty";
  }
  return summary.segments_indexed ? "ready" : "indexing";
}

/**
 * La detección de una canción del catálogo sin sus coincidencias consigo misma: lo que
 * interesa al elegirla son las otras canciones que se le parecen.
 */
export function withoutSong(detection: Detection, songId: number): Detection {
  const matches = detection.matches.filter((match) => match.song.id !== songId);
  return { ...detection, matches, detected: matches.some((match) => match.above_threshold) };
}
