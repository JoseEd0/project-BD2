import { useMemo } from "react";

import { formatMilliseconds, formatShare } from "../timing/formatDuration";
import { parseServerTiming, stageBreakdown, type StageBreakdown } from "../timing/serverTiming";
import { stageLabel, UNATTRIBUTED_LABEL } from "../timing/stageLabels";

interface StageTimingsProps {
  /** El valor crudo de la cabecera `Server-Timing`; `null` si no vino. */
  readonly serverTiming: string | null;
  readonly caption: string;
}

interface Row {
  readonly key: string;
  readonly label: string;
  readonly durationMs: number;
  readonly share: number;
  readonly tone: string;
}

const STAGE_TONES = 6;
const UNATTRIBUTED_TONE = "rest";

function rowsOf(breakdown: StageBreakdown): Row[] {
  const rows: Row[] = breakdown.stages.map((stage, index) => ({
    key: stage.name,
    label: stageLabel(stage),
    durationMs: stage.durationMs,
    share: stage.share,
    tone: String((index % STAGE_TONES) + 1),
  }));
  if (breakdown.unattributedMs > 0) {
    rows.push({
      key: UNATTRIBUTED_TONE,
      label: UNATTRIBUTED_LABEL,
      durationMs: breakdown.unattributedMs,
      share: breakdown.totalMs > 0 ? breakdown.unattributedMs / breakdown.totalMs : 0,
      tone: UNATTRIBUTED_TONE,
    });
  }
  return rows;
}

/** Dónde se fue cada milisegundo de una respuesta, según su `Server-Timing`. */
export function StageTimings({ serverTiming, caption }: StageTimingsProps) {
  const breakdown = useMemo(() => stageBreakdown(parseServerTiming(serverTiming)), [serverTiming]);
  if (breakdown === null) {
    return <p className="stage-timings-missing">El servidor no informó cuánto tardó cada etapa.</p>;
  }
  const total = (
    <figcaption className="stage-timings-caption">
      <span>{caption}</span>
      <strong className="numeric">{formatMilliseconds(breakdown.totalMs)} ms</strong>
    </figcaption>
  );
  // Solo el total (como en `/health`): no hay etapas que repartir.
  if (breakdown.stages.length === 0) {
    return <figure className="stage-timings">{total}</figure>;
  }
  const rows = rowsOf(breakdown);
  return (
    <figure className="stage-timings">
      {total}
      <div className="stage-bar" aria-hidden="true">
        {rows.map((row) => (
          <span key={row.key} className="stage-bar-segment" data-tone={row.tone} style={{ flexGrow: row.share }} />
        ))}
      </div>
      <table className="stage-table">
        <thead>
          <tr>
            <th scope="col">Etapa</th>
            <th scope="col">Tiempo</th>
            <th scope="col">Parte</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row">
                <span className="stage-swatch" data-tone={row.tone} aria-hidden="true" />
                {row.label}
              </th>
              <td className="numeric">{formatMilliseconds(row.durationMs)} ms</td>
              <td className="numeric">{formatShare(row.share)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
