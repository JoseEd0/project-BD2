import { parseServerTiming, stageBreakdown, stageTimingsOf, type StageTimings } from "@minigestor/web-kit";

import type { EngineTimings } from "../api/types";

/**
 * `segments` es la ida y vuelta al gestor y ya contiene `decode`, `extraction` y `search`,
 * que el servicio también anota: sumarlas otra vez inflaría el total. Se reparten aparte,
 * desde `engine_timings` del cuerpo, como el detalle de `segments`.
 */
export const ENGINE_STAGES: ReadonlySet<string> = new Set(["decode", "extraction", "search"]);

export const STAGE_LABELS: Readonly<Record<string, string>> = {
  probe: "Reconocer el formato",
  loudness: "Medir el silencio",
  segments: "Buscar en el gestor",
  voting: "Votación por desfase",
  songs: "Leer las canciones",
  decode: "Decodificar",
  extraction: "Sacar los MFCC",
  search: "Buscar en el HNSW",
};

export function stageLabel(key: string): string {
  return STAGE_LABELS[key] ?? key;
}

/** Las etapas del servicio sin las del gestor, que van dentro de `segments`. */
export function serviceStages(serverTiming: string | null): StageTimings | null {
  const metrics = parseServerTiming(serverTiming).filter((metric) => !ENGINE_STAGES.has(metric.name));
  const breakdown = stageBreakdown(metrics);
  return breakdown === null ? null : stageTimingsOf(breakdown);
}

export interface EngineStage {
  readonly key: string;
  readonly durationMs: number;
}

export function engineStages(timings: EngineTimings | null | undefined): readonly EngineStage[] {
  if (timings === null || timings === undefined) {
    return [];
  }
  return [
    { key: "decode", durationMs: timings.decode_ms },
    { key: "extraction", durationMs: timings.extraction_ms },
    { key: "search", durationMs: timings.search_ms },
  ];
}
