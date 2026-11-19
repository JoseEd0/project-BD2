import type { UnavailableReason } from "@minigestor/web-kit";

import { useServiceHealth } from "../api/useServiceHealth";

const UNAVAILABLE_MESSAGES: Record<UnavailableReason, string> = {
  unreachable: "El servicio de rostros no responde: puede estar apagado o reiniciándose.",
  timeout: "El servicio de rostros tarda demasiado en responder.",
  "http-error": "El servicio de rostros respondió con un error.",
  "unexpected-body": "Quien respondió no es el servicio de rostros.",
};

/** Una línea con el estado del servicio, y la forma de volver a preguntar si no está. */
export function ServiceLine() {
  const { data: health, refetch, isFetching } = useServiceHealth();
  if (health === undefined) {
    return (
      <p className="status-line" role="status">
        <span className="status-dot dot-warn" aria-hidden="true" />
        Comprobando el servicio…
      </p>
    );
  }
  if (health.state === "available") {
    return (
      <p className="status-line" role="status">
        <span className="status-dot dot-ok" aria-hidden="true" />
        Servicio disponible
      </p>
    );
  }
  return (
    <div className="notice notice-danger" role="alert">
      <p className="status-line">
        <span className="status-dot dot-off" aria-hidden="true" />
        <strong>Servicio no disponible.</strong>
      </p>
      <p>{UNAVAILABLE_MESSAGES[health.reason]}</p>
      <button type="button" className="button button-secondary" onClick={() => void refetch()} disabled={isFetching}>
        Volver a comprobar
      </button>
    </div>
  );
}
