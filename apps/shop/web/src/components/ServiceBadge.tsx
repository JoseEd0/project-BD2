import { useServiceHealth } from "../api/useServiceHealth";

/** El estado del servicio en la barra: un punto y una palabra, nunca solo el color. */
export function ServiceBadge() {
  const { data: health } = useServiceHealth();
  const state = health === undefined ? "checking" : health.state;
  const text = state === "available" ? "En línea" : state === "unavailable" ? "Sin servicio" : "Conectando…";
  return (
    <p className={`service service-${state}`} role="status">
      <span className="service-dot" aria-hidden="true" />
      <span className="service-text">{text}</span>
    </p>
  );
}
