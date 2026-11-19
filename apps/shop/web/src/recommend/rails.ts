import type { Recommendation, Recommendations, SignalQuery } from "../api/types";
import { SIGNALS, toSignal, type Signal } from "./signals";

/** Una recomendación vista desde una señal: cuánto aportó esa señal y con qué frase. */
export interface RailItem {
  readonly recommendation: Recommendation;
  readonly contribution: number;
  readonly reason: string;
}

/** Una fila por señal: la consulta que corrió (si corrió) y lo que aportó a la fusión. */
export interface SignalRail {
  readonly signal: Signal;
  readonly weight: number;
  readonly query: SignalQuery | null;
  readonly items: readonly RailItem[];
}

/**
 * Las filas de cada señal a partir de la lista fundida: cada recomendación entra en la fila
 * de cada señal que la propuso, ordenada por lo que esa señal le aportó (empates por el puesto
 * en la fusión). Las cuatro señales salen siempre, en su orden, aunque alguna no haya corrido.
 */
export function signalRails(response: Recommendations): SignalRail[] {
  return SIGNALS.map((signal) => {
    const items = response.recomendaciones
      .flatMap((recommendation, rank) => {
        const reason = recommendation.motivos.find((motivo) => toSignal(motivo.senal) === signal);
        return reason === undefined ? [] : [{ recommendation, contribution: reason.aporte, reason: reason.texto, rank }];
      })
      .sort((left, right) => right.contribution - left.contribution || left.rank - right.rank)
      .map(({ recommendation, contribution, reason }) => ({ recommendation, contribution, reason }));
    return {
      signal,
      weight: response.pesos[signal] ?? 0,
      query: response.senales.find((query) => toSignal(query.senal) === signal) ?? null,
      items,
    };
  });
}

/** Las señales de una recomendación, de la que más aportó a la que menos, sin repetir. */
export function stampsOf(recommendation: Recommendation): Signal[] {
  const ordered = [...recommendation.motivos].sort((left, right) => right.aporte - left.aporte);
  return [...new Set(ordered.map((motivo) => toSignal(motivo.senal)).filter((signal) => signal !== null))];
}

/** Los productos que están en `after` y no estaban en `before`: lo que cambió tras una compra. */
export function newlyRecommended(before: Recommendations | undefined, after: Recommendations | undefined): Set<string> {
  if (before === undefined || after === undefined) {
    return new Set();
  }
  const known = new Set(before.recomendaciones.map((item) => item.producto.id));
  return new Set(after.recomendaciones.map((item) => item.producto.id).filter((id) => !known.has(id)));
}

/** Cuánto pesó cada señal, en porcentaje entero, para decirlo junto a su fila. */
export function weightPercent(weight: number): number {
  return Math.round(weight * 100);
}
