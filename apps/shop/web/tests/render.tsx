import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";

import { ShopProviders } from "../src/App";

/** La app (o una pieza) con sus proveedores, en un router en memoria. */
export function renderAt(path: string, routes: RouteObject[]) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ShopProviders>
        <RouterProvider router={router} />
      </ShopProviders>
    </QueryClientProvider>,
  );
  return router;
}

export function renderPiece(piece: ReactNode) {
  return renderAt("/", [{ path: "/", element: piece }, { path: "*", element: null }]);
}
