import { Suspense } from "react";
import { Outlet } from "react-router";

import { useServiceHealth } from "../api/useServiceHealth";
import { useSinglePlayback } from "../player/usePlayer";
import { ServiceBadge } from "./ServiceBadge";
import { ThemeChoice } from "./ThemeChoice";
import { Wordmark } from "./Wordmark";

export function Layout() {
  const { data: health } = useServiceHealth();
  useSinglePlayback();
  return (
    <div className="shell">
      <a className="skip-link" href="#contenido">
        Ir al contenido
      </a>
      <header className="masthead">
        <div className="masthead__inner">
          <Wordmark />
          <div className="masthead__tools">
            <ServiceBadge health={health} />
            <ThemeChoice />
          </div>
        </div>
      </header>
      <main id="contenido" className="workspace" tabIndex={-1}>
        <Suspense fallback={<p role="status" className="loading-line">Abriendo el estudio…</p>}>
          <Outlet />
        </Suspense>
      </main>
      <footer className="colophon">
        Catálogo: Free Music Archive, <cite>fma_small</cite> (licencias Creative Commons de cada artista).
      </footer>
    </div>
  );
}
