import { Link } from "react-router";

import { APP } from "../identity";

export function NotFoundPage() {
  return (
    <section className="page-problem" aria-labelledby="not-found-title">
      <title>{`Página no encontrada · ${APP.name}`}</title>
      <h1 id="not-found-title">Esta dirección no existe</h1>
      <p>Puede que el enlace esté mal copiado.</p>
      <Link to="/">Ir al catálogo</Link>
    </section>
  );
}
