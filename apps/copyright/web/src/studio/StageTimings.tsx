import { formatMilliseconds, formatPercent } from "../analysis/format";
import { engineStages, serviceStages, stageLabel } from "../analysis/stages";
import type { EngineTimings } from "../api/types";

const UNATTRIBUTED_LABEL = "Red y servicio";
const ENGINE_STAGE_PARENT = "segments";

interface StageTimingsProps {
  readonly serverTiming: string | null;
  readonly engineTimings: EngineTimings | null | undefined;
}

/** Dónde se fue el tiempo del análisis: las etapas del servicio y, dentro del gestor, las suyas. */
export function StageTimings({ serverTiming, engineTimings }: StageTimingsProps) {
  const timings = serviceStages(serverTiming);
  if (timings === null || timings.rows.length === 0) {
    return null;
  }
  const engine = engineStages(engineTimings);
  return (
    <details className="timings">
      <summary>
        Cuánto tardó: <span className="measure">{formatMilliseconds(timings.totalMs)}</span>
      </summary>
      <div className="timings__bar" aria-hidden="true">
        {timings.rows.map((row, position) => (
          <span key={row.key} className="timings__slice" data-shade={position % 4} style={{ flexGrow: row.share }} />
        ))}
      </div>
      <table className="timings__table">
        <thead>
          <tr>
            <th scope="col">Etapa</th>
            <th scope="col">Tiempo</th>
            <th scope="col">Parte</th>
          </tr>
        </thead>
        <tbody>
          {timings.rows.flatMap((row) => {
            const rows = [
              <tr key={row.key}>
                <th scope="row">{row.unattributed ? UNATTRIBUTED_LABEL : stageLabel(row.key)}</th>
                <td className="measure">{formatMilliseconds(row.durationMs)}</td>
                <td className="measure">{formatPercent(row.share)}</td>
              </tr>,
            ];
            if (row.key === ENGINE_STAGE_PARENT) {
              rows.push(
                ...engine.map((stage) => (
                  <tr key={stage.key} className="timings__nested">
                    <th scope="row">{stageLabel(stage.key)}</th>
                    <td className="measure">{formatMilliseconds(stage.durationMs)}</td>
                    <td />
                  </tr>
                )),
              );
            }
            return rows;
          })}
        </tbody>
      </table>
    </details>
  );
}
