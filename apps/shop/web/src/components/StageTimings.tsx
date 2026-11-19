import { useStageTimings } from "@minigestor/web-kit";

import type { CacheStatus } from "../api/types";
import { formatMilliseconds } from "../format";
import { Icon } from "./Icon";

const STAGE_LABELS: Readonly<Record<string, string>> = {
  cache: "Caché (Redis)",
  gestor: "Gestor",
  clip: "CLIP",
  compras: "Compras conjuntas",
  visual: "Parecido visual",
  texto: "Descripción (BM25)",
  cercania: "Stock cercano",
};
const UNATTRIBUTED_LABEL = "Resto del servicio";

export const CACHE_WORDS: Readonly<Record<CacheStatus, string>> = {
  hit: "acierto",
  miss: "fallo",
  bypass: "sin caché",
};

interface StageTimingsProps {
  readonly serverTiming: string | null;
  readonly cache: CacheStatus | null;
}

/**
 * Dónde se fue el tiempo de la respuesta, por etapa, y si salió de la caché. Cada barra es
 * la parte del total que duró esa etapa; las tres señales que proponen corren a la vez, así
 * que sus barras se solapan en el tiempo y no se suman.
 */
export function StageTimings({ serverTiming, cache }: StageTimingsProps) {
  const timings = useStageTimings(serverTiming);
  return (
    <details className="timings">
      <summary>
        <Icon name="clock" size={18} />
        <span>{timings === null ? "Tiempos" : `Tiempos · ${formatMilliseconds(timings.totalMs)}`}</span>
        {cache !== null && <span className={`cache-word cache-${cache}`}>caché: {CACHE_WORDS[cache]}</span>}
      </summary>
      {timings === null || timings.rows.length === 0 ? (
        <p className="muted">El servicio no informó cuánto tardó cada etapa.</p>
      ) : (
        <table className="timings-table">
          <caption className="visually-hidden">Tiempo de cada etapa de la respuesta</caption>
          <tbody>
            {timings.rows.map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.unattributed ? UNATTRIBUTED_LABEL : (STAGE_LABELS[row.key] ?? row.key)}</th>
                <td className="timings-bar-cell">
                  <span className="timings-bar" style={{ inlineSize: `${Math.min(1, row.share) * 100}%` }} />
                </td>
                <td className="numeric">{formatMilliseconds(row.durationMs)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}
