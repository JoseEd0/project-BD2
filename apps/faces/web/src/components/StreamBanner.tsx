import { useServiceHealth } from "../api/useServiceHealth";
import type { StreamState } from "../realtime/stream";

/** Desde el segundo reintento seguido se explica qué pasa, con la salud del servicio. */
const ATTEMPTS_BEFORE_EXPLAINING = 2;

export interface StreamBannerProps {
  readonly state: StreamState;
  readonly onRetryNow: () => void;
}

/** Un aviso sobre la cámara cuando el reconocimiento no llega: réplica llena, servicio caído o red. */
export function StreamBanner({ state, onRetryNow }: StreamBannerProps) {
  const { data: health } = useServiceHealth();
  if (state.phase === "busy") {
    return (
      <div className="camera-banner" role="status">
        <strong>Hay mucha gente usando el reconocimiento</strong>
        <span>Te ponemos en la fila y reintentamos solos en unos segundos.</span>
      </div>
    );
  }
  if (state.phase !== "retrying" || state.attempt < ATTEMPTS_BEFORE_EXPLAINING) {
    return null;
  }
  const down = health?.state === "unavailable";
  return (
    <div className="camera-banner" role="alert">
      <strong>{down ? "El servicio de rostros no responde" : "Se cortó la conexión"}</strong>
      <span>{down ? "Puede estar reiniciándose. Seguimos intentando solos." : "Revisa tu Wi-Fi o tus datos. Seguimos intentando solos."}</span>
      <button type="button" className="button button-primary" onClick={onRetryNow}>
        Reintentar ahora
      </button>
    </div>
  );
}
