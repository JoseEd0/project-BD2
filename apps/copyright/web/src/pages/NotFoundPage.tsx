import { Link } from "react-router";

import { APP } from "../identity";

export function NotFoundPage() {
  return (
    <section aria-labelledby="not-found-title">
      <title>{`Página no encontrada | ${APP.name}`}</title>
      <h1 id="not-found-title">Esta dirección no existe</h1>
      <Link to="/">Ir al inicio</Link>
    </section>
  );
}
