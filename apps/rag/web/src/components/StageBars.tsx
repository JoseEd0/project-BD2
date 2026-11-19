import { useStageTimings } from "@minigestor/web-kit";

import { formatDuration, formatShare } from "../format";

const UNATTRIBUTED_LABEL = "Fuera de las etapas";

interface StageBarsProps {
  readonly serverTiming: string | null;
  readonly labels: Readonly<Record<string, string>>;
  readonly caption: string;
}

/**
 * El `Server-Timing` de una respuesta como una barra por etapa, proporcional al total. Las
 * etapas que corren en paralelo (BM25 y HNSW en la búsqueda híbrida) pueden sumar más que el
 * total: cada barra es su parte del total, no un tramo de una línea de tiempo.
 */
export function StageBars({ serverTiming, labels, caption }: StageBarsProps) {
  const timings = useStageTimings(serverTiming);
  if (timings === null) {
    return <p className="muted">El servidor no informó cuánto tardó cada etapa.</p>;
  }
  return (
    <table className="stages">
      <caption>
        {caption}: <strong>{formatDuration(timings.totalMs)}</strong>
      </caption>
      <tbody>
        {timings.rows.map((row) => (
          <tr key={row.key} data-unattributed={row.unattributed || undefined}>
            <th scope="row">{row.unattributed ? UNATTRIBUTED_LABEL : (labels[row.key] ?? row.description ?? row.key)}</th>
            <td className="stages__bar" aria-hidden="true">
              <span style={{ inlineSize: `${Math.min(1, row.share) * 100}%` }} />
            </td>
            <td className="num">{formatDuration(row.durationMs)}</td>
            <td className="num num__score">{formatShare(row.share)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
