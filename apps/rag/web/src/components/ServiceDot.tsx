import type { UnavailableReason } from "@minigestor/web-kit";

import { useServiceHealth } from "../api/useServiceHealth";

const UNAVAILABLE: Record<UnavailableReason, string> = {
  unreachable: "Sin conexión con el servicio",
  timeout: "El servicio no responde a tiempo",
  "http-error": "El servicio responde con errores",
  "unexpected-body": "El servicio no está detrás de esta dirección",
};

/** El estado del API en una línea: el detalle importa solo cuando algo falla. */
export function ServiceDot() {
  const { data: health } = useServiceHealth();
  if (health === undefined) {
    return (
      <p className="service" data-state="checking" role="status">
        Conectando…
      </p>
    );
  }
  const available = health.state === "available";
  return (
    <p className="service" data-state={available ? "up" : "down"} role="status">
      {available ? "En línea" : UNAVAILABLE[health.reason]}
    </p>
  );
}
