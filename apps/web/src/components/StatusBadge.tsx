import type { ServiceHealth } from "../services/health";

type Status = "checking" | ServiceHealth["state"];

const STATUS_LABELS: Record<Status, string> = {
  checking: "Comprobando",
  available: "Disponible",
  unavailable: "No disponible",
};

/** El estado de un servicio con texto, no solo color. `undefined` mientras se comprueba. */
export function StatusBadge({ health }: { readonly health: ServiceHealth | undefined }) {
  const status: Status = health?.state ?? "checking";
  return (
    <span className="status-badge" data-status={status}>
      <span className="status-dot" aria-hidden="true" />
      {STATUS_LABELS[status]}
    </span>
  );
}
