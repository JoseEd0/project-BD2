import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <section className="page route-error" aria-labelledby="not-found-title">
      <title>Página no encontrada | Aplicaciones del Minigestor</title>
      <h1 id="not-found-title">Esta dirección no lleva a ninguna aplicación</h1>
      <p>Elige una en la barra de arriba o vuelve al inicio para ver todas.</p>
      <div className="actions">
        <Link className="button" to="/">
          Ir al inicio
        </Link>
      </div>
    </section>
  );
}
