import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { lazy } from "react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import { describe, expect, it } from "vitest";

import { Layout } from "../src/components/Layout";
import { RouteErrorBoundary } from "../src/components/RouteErrorBoundary";
import { routes } from "../src/routes";
import { PORTAL_APPS } from "../src/services/catalog";
import { jsonResponse, stubFetch } from "./support/fetch";

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

function onlyEngineUp(): void {
  stubFetch((url) =>
    url.pathname === "/api/health"
      ? jsonResponse({ status: "ok", version: "0.1.0" }, { headers: { "Server-Timing": "exec;dur=1, total;dur=2" } })
      : Promise.reject(new TypeError("Failed to fetch")),
  );
}

describe("routes", () => {
  it("lists the four apps on the home page with each service's live status", async () => {
    onlyEngineUp();
    renderAt("/");
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    const lanes = within(screen.getByRole("list", { name: /Aplicaciones/ })).getAllByRole("link");
    expect(lanes.map((lane) => lane.getAttribute("href"))).toEqual(PORTAL_APPS.map((app) => `/${app.slug}`));
    await waitFor(() => expect(screen.getAllByText("No disponible")).toHaveLength(PORTAL_APPS.length));
    expect(await screen.findByText("Disponible")).toBeTruthy();
    expect(await screen.findByText("Última comprobación de salud")).toBeTruthy();
  });

  it.each(PORTAL_APPS)("opens $name at /$slug with an honest empty state", async (app) => {
    onlyEngineUp();
    renderAt(`/${app.slug}`);
    expect(await screen.findByRole("heading", { level: 1, name: app.name })).toBeTruthy();
    expect(
      await screen.findByText("No hay respuesta: el servicio no está desplegado en este entorno o está caído."),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Volver a comprobar" })).toBeTruthy();
  });

  it("says when the service answers but its interface is not there yet", async () => {
    stubFetch(() => jsonResponse({ status: "ok" }));
    renderAt("/rostros");
    expect(await screen.findByText(/El servicio ya responde/)).toBeTruthy();
  });

  it("navigates between apps from the bar and marks the current one", async () => {
    onlyEngineUp();
    const user = userEvent.setup();
    const router = renderAt("/");
    const navigation = await screen.findByRole("navigation", { name: "Aplicaciones" });
    await user.click(within(navigation).getByRole("link", { name: /Papers/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "Preguntas a papers" })).toBeTruthy();
    expect(router.state.location.pathname).toBe("/papers");
    expect(within(navigation).getByRole("link", { name: /Papers/ }).getAttribute("aria-current")).toBe("page");
  });

  it("answers an unknown path with a way back", async () => {
    onlyEngineUp();
    renderAt("/no-existe");
    expect(await screen.findByRole("heading", { name: "Esta dirección no lleva a ninguna aplicación" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ir al inicio" })).toBeTruthy();
  });

  it("keeps a failing route inside its own boundary, with the bar still usable", async () => {
    onlyEngineUp();
    const broken: RouteObject[] = [
      {
        path: "/",
        Component: Layout,
        children: [
          {
            path: "roto",
            Component: lazy(() => Promise.reject(new Error("chunk missing"))),
            ErrorBoundary: RouteErrorBoundary,
          },
        ],
      },
    ];
    renderAt("/roto", broken);
    expect(await screen.findByRole("heading", { name: "Esta página falló al cargar" })).toBeTruthy();
    expect(screen.getByText("chunk missing")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Aplicaciones" })).toBeTruthy();
  });
});
