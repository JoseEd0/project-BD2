import { jsonResponse } from "@minigestor/web-kit/testing";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { lazy } from "react";
import type { RouteObject } from "react-router";
import { describe, expect, it } from "vitest";

import { Layout } from "../src/components/Layout";
import { RouteErrorBoundary } from "../src/components/RouteErrorBoundary";
import { APP } from "../src/identity";
import { FACETS, routeFetch } from "./api";
import { renderAt } from "./render";

function serviceUp(): void {
  routeFetch((request) => {
    const path = new URL(request.url).pathname;
    if (path === "/api/health") {
      return jsonResponse({ status: "ok", version: "0.1.0" });
    }
    return path === "/api/facets" ? jsonResponse(FACETS) : new Response(null, { status: 404 });
  });
}

describe("the app shell", () => {
  it("shows the corpus scope, the service state and example questions", async () => {
    serviceUp();
    renderAt("/");
    expect(await screen.findByText("1493 documentos")).toBeTruthy();
    expect(await screen.findByText("En línea")).toBeTruthy();
    expect(await screen.findByRole("heading", { level: 1, name: /Qué quieres saber/ })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /\?|vector databases/ }).length).toBeGreaterThanOrEqual(3);
  });

  it("says when the service cannot be reached", async () => {
    routeFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    renderAt("/");
    expect(await screen.findByText("Sin conexión con el servicio")).toBeTruthy();
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
    expect(screen.getByRole("link", { name: `${APP.name}, inicio` })).toBeTruthy();
  });
});
