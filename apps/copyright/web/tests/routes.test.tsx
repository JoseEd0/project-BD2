import { jsonResponse, stubFetch } from "@minigestor/web-kit/testing";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { lazy } from "react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import { describe, expect, it } from "vitest";

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
}

function serviceUp(): void {
  stubFetch((url) =>
    url.pathname === "/api/health" ? jsonResponse({ status: "ok", version: "0.1.0" }) : Promise.reject(new TypeError("Failed to fetch")),
  );
}

describe("routes", () => {
  it("invites to upload when the service is up", async () => {
    serviceUp();
    renderAt("/");
    expect(await screen.findByRole("heading", { level: 1, name: "¿Esta canción usa música del catálogo?" })).toBeTruthy();
    expect(await screen.findByText("En línea")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Elegir un audio" })).toHaveProperty("disabled", false);
  });

  it("says why the service is down and waits before accepting a file", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    renderAt("/");
    expect(await screen.findByText(/no está desplegado aquí o está caído/)).toBeTruthy();
    expect(screen.getByText("Sin servicio")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Elegir un audio" })).toHaveProperty("disabled", true);
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
        children: [{ path: "roto", Component: lazy(() => Promise.reject(new Error("chunk missing"))), ErrorBoundary: RouteErrorBoundary }],
      },
    ];
    renderAt("/roto", broken);
    expect(await screen.findByRole("heading", { name: "Esta página falló al cargar" })).toBeTruthy();
    expect(screen.getByText("chunk missing")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Songcheck/ })).toBeTruthy();
  });
});
