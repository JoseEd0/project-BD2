import { jsonResponse, stubFetch } from "@minigestor/web-kit/testing";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { lazy } from "react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import { describe, expect, it } from "vitest";

import { APP } from "../src/identity";
import { Layout } from "../src/components/Layout";
import { RouteErrorBoundary } from "../src/components/RouteErrorBoundary";
import { routes } from "../src/routes";

function renderAt(path: string, routeTable: RouteObject[] = routes) {
  const router = createMemoryRouter(routeTable, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function serviceUp(): void {
  stubFetch((url) =>
    url.pathname === "/api/health"
      ? jsonResponse({ status: "ok", version: "0.1.0" }, { headers: { "Server-Timing": "exec;dur=1, total;dur=2" } })
      : Promise.reject(new TypeError("Failed to fetch")),
  );
}

describe("routes", () => {
  it("shows the service as available, with its stage timings", async () => {
    serviceUp();
    renderAt("/");
    expect(await screen.findByRole("heading", { level: 1, name: APP.name })).toBeTruthy();
    expect(await screen.findByText("Disponible")).toBeTruthy();
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows.map((row) => row.textContent?.replace(/\s/g, " "))).toEqual(["exec1 ms50 %", "Fuera de las etapas1 ms50 %"]);
  });

  it("says why the service is unavailable and lets the user ask again", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    renderAt("/");
    expect(await screen.findByText(/no está desplegado en este entorno/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Volver a comprobar" })).toBeTruthy();
  });

  it("applies and remembers the chosen theme", async () => {
    serviceUp();
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await screen.findByRole("radio", { name: "Oscuro" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("answers an unknown path with a way back", async () => {
    serviceUp();
    renderAt("/no-existe");
    expect(await screen.findByRole("heading", { name: "Esta dirección no existe" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ir al inicio" })).toBeTruthy();
  });

  it("keeps a failing route inside its own boundary", async () => {
    serviceUp();
    const broken: RouteObject[] = [
      {
        path: "/",
        Component: Layout,
        children: [
          { path: "roto", Component: lazy(() => Promise.reject(new Error("chunk missing"))), ErrorBoundary: RouteErrorBoundary },
        ],
      },
    ];
    renderAt("/roto", broken);
    expect(await screen.findByRole("heading", { name: "Esta página falló al cargar" })).toBeTruthy();
    expect(screen.getByText("chunk missing")).toBeTruthy();
    expect(screen.getByRole("link", { name: APP.name })).toBeTruthy();
  });
});
