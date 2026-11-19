import { lazy } from "react";
import type { RouteObject } from "react-router";

import { Layout } from "./components/Layout";
import { RouteErrorBoundary } from "./components/RouteErrorBoundary";
import { NotFoundPage } from "./pages/NotFoundPage";

/*
 * Cada página en su propio fragmento del bundle, con `React.lazy` y no con el `lazy` de las
 * rutas: si el fragmento no llega (una versión nueva desplegada con la pestaña abierta), el
 * `lazy` de React Router deja la ruta en blanco, mientras que el error de `React.lazy` lo
 * atrapa el límite de error de esa ruta.
 */
const ResearchPage = lazy(() => import("./pages/ResearchPage"));

export const routes: RouteObject[] = [
  {
    path: "/",
    Component: Layout,
    ErrorBoundary: RouteErrorBoundary,
    children: [
      { index: true, Component: ResearchPage, ErrorBoundary: RouteErrorBoundary },
      { path: "*", Component: NotFoundPage },
    ],
  },
];
