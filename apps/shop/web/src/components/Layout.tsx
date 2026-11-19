import { Suspense } from "react";
import { Link, NavLink, Outlet } from "react-router";

import { CustomerPicker } from "./CustomerPicker";
import { SearchBox } from "./SearchBox";
import { ServiceBadge } from "./ServiceBadge";
import { ServiceDown } from "./ServiceDown";
import { ThemeChoice } from "./ThemeChoice";
import { Wordmark } from "./Wordmark";

export function Layout() {
  return (
    <div className="shell">
      <a className="skip" href="#contenido">
        Saltar al contenido
      </a>
      <header className="bar">
        <div className="bar-row">
          <Link to="/" className="bar-home" aria-label="Cajón, ir al catálogo">
            <Wordmark />
          </Link>
          <nav className="bar-nav" aria-label="Secciones">
            <NavLink to="/" end>
              Catálogo
            </NavLink>
            <NavLink to="/para-ti">Para ti</NavLink>
          </nav>
          <div className="bar-tools">
            <ServiceBadge />
            <CustomerPicker />
            <ThemeChoice />
          </div>
        </div>
        <SearchBox />
      </header>
      <main id="contenido" tabIndex={-1}>
        <ServiceDown />
        <Suspense
          fallback={
            <p className="page-loading" role="status">
              Cargando…
            </p>
          }
        >
          <Outlet />
        </Suspense>
      </main>
      <footer className="footer">
        <p>
          Catálogo de Amazon Reviews 2023 (instrumentos musicales), fichas en inglés y precios en dólares; tiendas de
          OpenStreetMap en Lima con inventario simulado. Las compras son simuladas.
        </p>
      </footer>
    </div>
  );
}
