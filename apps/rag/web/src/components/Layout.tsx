import { Suspense } from "react";
import { Link, Outlet } from "react-router";

import { formatCount } from "../format";
import { APP } from "../identity";
import { useFacets } from "../research/useRetrieval";
import { ServiceDot } from "./ServiceDot";
import { ThemeChoice } from "./ThemeChoice";

function CorpusScope() {
  const { data } = useFacets();
  if (data === undefined) {
    return null;
  }
  const papers = data.categories.reduce((sum, category) => sum + category.papers, 0);
  const categories = data.categories.map((category) => category.category).join(" · ");
  return (
    <p className="strip__scope">
      <span>{formatCount(papers)} documentos</span>
      <span className="strip__categories">{categories}</span>
    </p>
  );
}

/** La franja de título, como la cabecera legible a simple vista de una microficha. */
export function Layout() {
  return (
    <div className="app">
      <a className="skip-link" href="#pregunta">
        Ir a la pregunta
      </a>
      <header className="strip">
        {/* La conversación vive en el estado de la página: volver al inicio sin recargar la dejaría igual. */}
        <Link to="/" reloadDocument className="strip__mark" aria-label={`${APP.name}, nueva conversación`} title="Nueva conversación">
          {APP.name}
        </Link>
        <CorpusScope />
        <div className="strip__end">
          <ServiceDot />
          <ThemeChoice />
        </div>
      </header>
      <Suspense fallback={<p role="status" className="loading-page">Cargando…</p>}>
        <Outlet />
      </Suspense>
    </div>
  );
}
