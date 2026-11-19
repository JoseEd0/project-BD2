import type { ServiceHealth, UnavailableReason } from "@minigestor/web-kit";

export const UNAVAILABLE_REASONS: Record<UnavailableReason, string> = {
  unreachable: "no hay respuesta: el servicio no está desplegado aquí o está caído",
  timeout: "el servicio tardó demasiado en responder",
  "http-error": "el servicio respondió con un error",
  "unexpected-body": "quien respondió no es el servicio de detección",
};

/** El estado del servicio en la cabecera: un punto y su texto, nunca solo el color. */
export function ServiceBadge({ health }: { readonly health: ServiceHealth | undefined }) {
  if (health === undefined) {
    return (
      <p className="service-badge" data-state="checking">
        <span className="service-badge__dot" aria-hidden="true" />
        Comprobando
      </p>
    );
  }
  const available = health.state === "available";
  return (
    <p className="service-badge" data-state={available ? "up" : "down"} title={available ? undefined : UNAVAILABLE_REASONS[health.reason]}>
      <span className="service-badge__dot" aria-hidden="true" />
      {available ? "En línea" : "Sin servicio"}
    </p>
  );
}
