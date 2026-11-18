import { isRouteErrorResponse, Link, useRouteError } from "react-router";

function describe(error: unknown): string {
  if (isRouteErrorResponse(error)) {
    return `${error.status} ${error.statusText}`;
  }
  return error instanceof Error ? error.message : String(error);
}

/** El fallo de una ruta queda en esa ruta: la cabecera sigue a mano. */
export function RouteErrorBoundary() {
  const error = useRouteError();
  return (
    <section aria-labelledby="route-error-title">
      <h1 id="route-error-title">Esta página falló al cargar</h1>
      <p>Recargar suele bastar si la aplicación se actualizó mientras la tenías abierta.</p>
      <pre>{describe(error)}</pre>
      <button type="button" onClick={() => window.location.reload()}>
        Recargar
      </button>
      <Link to="/">Ir al inicio</Link>
    </section>
  );
}
