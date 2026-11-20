import type { CatalogSummary, DetectedMatch, Detection } from "../api/types";

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

/** Cuántas coincidencias débiles se muestran si ninguna pasa el umbral. */
export const WEAK_MATCHES_SHOWN = 3;

/**
 * Las coincidencias que merecen verse de entrada: las que pasan el umbral o, si ninguna lo
 * pasa, las mejores con alguna evidencia. Las demás quedan tras «Mostrar coincidencias débiles».
 */
export function notableMatches(matches: readonly DetectedMatch[]): readonly DetectedMatch[] {
  const strong = matches.filter((match) => match.above_threshold);
  if (strong.length > 0) {
    return strong;
  }
  return matches.filter((match) => match.score > 0).slice(0, WEAK_MATCHES_SHOWN);
}
