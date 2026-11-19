import { useStageTimings, type UnavailableReason } from "@minigestor/web-kit";

import { useServiceHealth } from "../api/useServiceHealth";

const UNAVAILABLE_MESSAGES: Record<UnavailableReason, string> = {
  unreachable: "No hay respuesta: el servicio no está desplegado en este entorno o está caído.",
  timeout: "El servicio tardó demasiado en responder.",
  "http-error": "El servicio respondió con un error.",
  "unexpected-body": "Quien respondió no es el servicio: no está desplegado detrás de esta dirección.",
};

const UNATTRIBUTED_LABEL = "Fuera de las etapas";
const MILLISECONDS = new Intl.NumberFormat("es", { maximumFractionDigits: 2 });
const SHARE = new Intl.NumberFormat("es", { style: "percent", maximumFractionDigits: 0 });

function StageTimingTable({ serverTiming }: { readonly serverTiming: string | null }) {
  const timings = useStageTimings(serverTiming);
  if (timings === null) {
    return <p className="muted">El servidor no informó cuánto tardó cada etapa.</p>;
  }
  return (
    <table>
      <caption>Última comprobación: {MILLISECONDS.format(timings.totalMs)} ms</caption>
      <tbody>
        {timings.rows.map((row) => (
          <tr key={row.key}>
            <th scope="row">{row.unattributed ? UNATTRIBUTED_LABEL : (row.description ?? row.key)}</th>
            <td>{MILLISECONDS.format(row.durationMs)} ms</td>
            <td>{SHARE.format(row.share)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** El estado del API de la app, con el motivo cuando no está y sus tiempos cuando sí. */
export function ServiceStatus() {
  const { data: health, refetch, isFetching } = useServiceHealth();
  if (health === undefined) {
    return <p role="status">Comprobando el servicio…</p>;
  }
  if (health.state === "unavailable") {
    return (
      <section aria-label="Estado del servicio">
        <p>
          <strong>No disponible.</strong> {UNAVAILABLE_MESSAGES[health.reason]}
        </p>
        <button type="button" onClick={() => void refetch()} disabled={isFetching}>
          Volver a comprobar
        </button>
      </section>
    );
  }
  return (
    <section aria-label="Estado del servicio">
      <p>
        <strong>Disponible</strong>
        {health.version !== null && <span className="muted"> · versión {health.version}</span>}
      </p>
      <StageTimingTable serverTiming={health.serverTiming} />
    </section>
  );
}
