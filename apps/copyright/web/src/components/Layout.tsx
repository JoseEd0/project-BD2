import { Suspense } from "react";
import { Link, Outlet } from "react-router";

import { APP } from "../identity";
import { ThemeChoice } from "./ThemeChoice";

export function Layout() {
  return (
    <div className="shell">
      <header>
        <Link to="/">{APP.name}</Link>
        <ThemeChoice />
      </header>
      <main id="contenido">
        <Suspense fallback={<p role="status">Cargando…</p>}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
