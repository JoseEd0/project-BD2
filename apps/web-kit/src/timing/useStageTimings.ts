import { useMemo } from "react";

import { parseServerTiming, stageBreakdown, type StageBreakdown } from "./serverTiming.ts";

/** Una fila para pintar: una etapa medida o el resto que ninguna etapa explica. */
export interface StageTimingRow {
  /** El nombre de la métrica, o `UNATTRIBUTED_KEY` para el resto. */
  readonly key: string;
  readonly description: string | null;
  readonly durationMs: number;
  /** Parte del total, entre 0 y 1. */
  readonly share: number;
  readonly unattributed: boolean;
}

export interface StageTimings {
  readonly totalMs: number;
  /** Vacío si la cabecera solo trae el total (como en `/health`): no hay nada que repartir. */
  readonly rows: readonly StageTimingRow[];
}

export const UNATTRIBUTED_KEY = "unattributed";

/** Las filas de un reparto, con el tiempo sin atribuir al final si lo hay. */
export function stageTimingsOf(breakdown: StageBreakdown): StageTimings {
  const rows: StageTimingRow[] = breakdown.stages.map((stage) => ({
    key: stage.name,
    description: stage.description,
    durationMs: stage.durationMs,
    share: stage.share,
    unattributed: false,
  }));
  if (breakdown.stages.length > 0 && breakdown.unattributedMs > 0) {
    rows.push({
      key: UNATTRIBUTED_KEY,
      description: null,
      durationMs: breakdown.unattributedMs,
      share: breakdown.totalMs > 0 ? breakdown.unattributedMs / breakdown.totalMs : 0,
      unattributed: true,
    });
  }
  return { totalMs: breakdown.totalMs, rows };
}

/**
 * Dónde se fue cada milisegundo de una respuesta, según su `Server-Timing`, listo para que
 * cada app lo pinte a su manera. `null` si la cabecera no vino o no trae ninguna duración.
 */
export function useStageTimings(serverTiming: string | null | undefined): StageTimings | null {
  return useMemo(() => {
    const breakdown = stageBreakdown(parseServerTiming(serverTiming));
    return breakdown === null ? null : stageTimingsOf(breakdown);
  }, [serverTiming]);
}
