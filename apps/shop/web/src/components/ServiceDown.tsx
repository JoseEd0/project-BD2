import type { UnavailableReason } from "@minigestor/web-kit";

import { useServiceHealth } from "../api/useServiceHealth";
import { ProblemNotice } from "./ProblemNotice";

const REASONS: Record<UnavailableReason, string> = {
  unreachable: "No hay respuesta del servicio de la tienda: no está desplegado aquí o está caído.",
  timeout: "El servicio de la tienda tardó demasiado en responder.",
  "http-error": "El servicio de la tienda respondió con un error.",
  "unexpected-body": "Quien respondió no es el servicio de la tienda.",
};

/** Si el servicio no está, se dice arriba de todo, con el motivo y para volver a probar. */
export function ServiceDown() {
  const { data: health, refetch, isFetching } = useServiceHealth();
  if (health?.state !== "unavailable") {
    return null;
  }
  return (
    <div className="service-down">
      <ProblemNotice
        title="La tienda no está disponible"
        detail={`${REASONS[health.reason]} Las búsquedas y las recomendaciones vuelven cuando responda.`}
        onRetry={() => void refetch()}
        retrying={isFetching}
      />
    </div>
  );
}
