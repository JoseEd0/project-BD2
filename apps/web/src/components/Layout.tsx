import { Suspense, useEffect, useRef, type RefObject } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import { PORTAL_APPS } from "../services/catalog";
import { AppGlyph } from "./AppGlyph";
import { OfflineNotice } from "./OfflineNotice";
import { RouteLoading } from "./RouteLoading";
import { ThemeSwitch } from "./ThemeSwitch";

/** Al cambiar de página, el foco va al contenido nuevo: un lector de pantalla lo anuncia y
 * el tabulador no vuelve a empezar por la barra. */
function useFocusOnNavigation(): RefObject<HTMLElement | null> {
  const main = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    main.current?.focus();
  }, [pathname]);
  return main;
}

function AppNavigation() {
  return (
    <nav className="app-nav" aria-label="Aplicaciones">
      <ul>
        {PORTAL_APPS.map((app) => (
          <li key={app.id}>
            <NavLink to={`/${app.slug}`} className="app-nav-link" data-app={app.id}>
              <AppGlyph service={app.id} />
              <span>{app.shortName}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function Layout() {
  const main = useFocusOnNavigation();
  return (
    <div className="shell">
      <a className="skip-link" href="#contenido">
        Saltar al contenido
      </a>
      <header className="topbar">
        <Link to="/" className="wordmark" aria-label="Minigestor, inicio">
          <span className="wordmark-mark" aria-hidden="true" />
          <span>minigestor</span>
        </Link>
        <AppNavigation />
        <ThemeSwitch />
      </header>
      <OfflineNotice />
      <main id="contenido" ref={main} tabIndex={-1}>
        <Suspense fallback={<RouteLoading />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
