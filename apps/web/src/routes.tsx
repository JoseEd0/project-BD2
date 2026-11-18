import { lazy, type ComponentType } from "react";
import type { RouteObject } from "react-router";

import { Layout } from "./components/Layout";
import { RouteErrorBoundary } from "./components/RouteErrorBoundary";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PORTAL_APPS, type AppId } from "./services/catalog";

/*
 * Cada página en su propio fragmento del bundle, con `React.lazy` y no con el `lazy` de las
 * rutas: si el fragmento no llega (una versión nueva desplegada con la pestaña abierta), el
 * `lazy` de React Router deja la ruta en blanco, mientras que el error de `React.lazy` lo
 * atrapa el límite de error de esa ruta.
 */
const HomePage = lazy(() => import("./pages/HomePage"));

const APP_PAGES: Record<AppId, ComponentType> = {
  copyright: lazy(() => import("./pages/copyright/CopyrightPage")),
  shop: lazy(() => import("./pages/shop/ShopPage")),
  papers: lazy(() => import("./pages/papers/PapersPage")),
  faces: lazy(() => import("./pages/faces/FacesPage")),
};

export const routes: RouteObject[] = [
  {
    path: "/",
    Component: Layout,
    // Si falla la propia barra, no hay dentro de qué mostrar el error: se muestra solo.
    ErrorBoundary: RouteErrorBoundary,
    children: [
      { index: true, Component: HomePage, ErrorBoundary: RouteErrorBoundary },
      ...PORTAL_APPS.map((app) => ({ path: app.slug, Component: APP_PAGES[app.id], ErrorBoundary: RouteErrorBoundary })),
      { path: "*", Component: NotFoundPage },
    ],
  },
];
