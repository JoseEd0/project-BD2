import { isRouteErrorResponse, useRouteError } from "react-router";

import { TextScreen } from "./TextScreen";

function describe(error: unknown): string {
  if (isRouteErrorResponse(error)) {
    return `${error.status} ${error.statusText}`;
  }
  return error instanceof Error ? error.message : String(error);
}

/** El fallo de una pantalla queda en esa pantalla, con salida. */
export function RouteErrorBoundary() {
  const error = useRouteError();
  return (
    <TextScreen
      title="Esta pantalla falló al cargar"
      back={{ to: "/", label: "Ir al inicio" }}
      actions={
        <button type="button" className="button button-primary button-block" onClick={() => window.location.reload()}>
          Recargar
        </button>
      }
    >
      <p className="lead">Recargar suele bastar si la app se actualizó mientras la tenías abierta.</p>
      <p className="muted">Detalle: {describe(error)}</p>
    </TextScreen>
  );
}
