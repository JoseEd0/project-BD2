import { lazy, Suspense } from "react";
import { Outlet, type RouteObject } from "react-router";

import { RouteErrorBoundary } from "./components/RouteErrorBoundary";
import { NotFoundPage } from "./pages/NotFoundPage";

/*
 * Cada pantalla en su propio fragmento del bundle, con `React.lazy` y no con el `lazy` de las
 * rutas: si el fragmento no llega (una versión nueva desplegada con la pestaña abierta), el
 * `lazy` de React Router deja la ruta en blanco, mientras que el error de `React.lazy` lo
 * atrapa el límite de error de esa ruta.
 */
const HomePage = lazy(() => import("./pages/HomePage"));
const EnrollPage = lazy(() => import("./pages/EnrollPage"));
const RecognizePage = lazy(() => import("./pages/RecognizePage"));
const MyDataPage = lazy(() => import("./pages/MyDataPage"));
const LookalikesPage = lazy(() => import("./pages/LookalikesPage"));

function Shell() {
  return (
    <>
      <a className="skip-link" href="#contenido">
        Saltar al contenido
      </a>
      <Suspense
        fallback={
          <p className="loading" role="status">
            <span className="spinner" aria-hidden="true" />
            Cargando…
          </p>
        }
      >
        <Outlet />
      </Suspense>
    </>
  );
}

export const ROUTES = { home: "/", enroll: "/registro", recognize: "/reconocer", lookalikes: "/famosos", myData: "/mis-datos" } as const;

export const routes: RouteObject[] = [
  {
    path: "/",
    Component: Shell,
    ErrorBoundary: RouteErrorBoundary,
    children: [
      { index: true, Component: HomePage, ErrorBoundary: RouteErrorBoundary },
      { path: "registro", Component: EnrollPage, ErrorBoundary: RouteErrorBoundary },
      { path: "reconocer", Component: RecognizePage, ErrorBoundary: RouteErrorBoundary },
      { path: "famosos", Component: LookalikesPage, ErrorBoundary: RouteErrorBoundary },
      { path: "mis-datos", Component: MyDataPage, ErrorBoundary: RouteErrorBoundary },
      { path: "*", Component: NotFoundPage },
    ],
  },
];
