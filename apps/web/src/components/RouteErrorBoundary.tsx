import { isRouteErrorResponse, Link, useRouteError } from "react-router";

function describe(error: unknown): string {
  if (isRouteErrorResponse(error)) {
    return `${error.status} ${error.statusText}`;
  }
  return error instanceof Error ? error.message : String(error);
}

/** El fallo de una ruta queda en esa ruta: la barra y las demás aplicaciones siguen a mano. */
export function RouteErrorBoundary() {
  const error = useRouteError();
  return (
    <section className="page route-error" aria-labelledby="route-error-title">
      <h1 id="route-error-title">Esta página falló al cargar</h1>
      <p>
        Recargar suele bastar si el portal se actualizó mientras lo tenías abierto. Si vuelve a pasar, el detalle de
        abajo dice qué se rompió.
      </p>
      <pre className="route-error-detail">{describe(error)}</pre>
      <div className="actions">
        <button type="button" className="button" onClick={() => window.location.reload()}>
          Recargar
        </button>
        <Link className="button button-quiet" to="/">
          Ir al inicio
        </Link>
      </div>
    </section>
  );
}
