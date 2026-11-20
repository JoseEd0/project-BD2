import { jsonResponse, stubFetch } from "@minigestor/web-kit/testing";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { routes } from "../src/routes";
import { product } from "./fixtures";
import { renderAt } from "./render";

const HEALTHY = { status: "ok", version: "0.1.0" };

function shop(answers: Record<string, () => Response>): void {
  stubFetch((url) => {
    const answer = answers[url.pathname];
    if (answer !== undefined) {
      return answer();
    }
    return url.pathname === "/api/health" ? jsonResponse(HEALTHY) : jsonResponse([]);
  });
}

describe("the catalog", () => {
  it("searches by words and says how it ranked", async () => {
    shop({
      "/api/busqueda/productos": () =>
        jsonResponse(
          {
            consulta: "guitar strings",
            corregida: null,
            resultados: [{ producto: product("B000STRING", { titulo: "Acoustic Guitar Strings" }), puntaje: 5.17 }],
          },
          { headers: { "Server-Timing": "gestor;dur=6, total;dur=7" } },
        ),
    });
    renderAt("/?q=guitar+strings", routes);
    expect(await screen.findByRole("heading", { level: 1, name: "«guitar strings»" })).toBeTruthy();
    const results = screen.getByRole("region", { name: "«guitar strings»" });
    expect(await within(results).findByText("Acoustic Guitar Strings")).toBeTruthy();
    expect(within(results).getByText("BM25 5.17")).toBeTruthy();
    expect(within(results).getByText(/1 producto por las palabras de la ficha/)).toBeTruthy();
  });

  it("says which corrected words it searched and offers the exact ones", async () => {
    shop({
      "/api/busqueda/productos": () =>
        jsonResponse({
          consulta: "guitr",
          corregida: "guitar",
          resultados: [{ producto: product("B000GUITAR", { titulo: "Electric Guitar" }), puntaje: 3.1 }],
        }),
    });
    const user = userEvent.setup();
    const router = renderAt("/?q=guitr", routes);
    expect(await screen.findByText("Electric Guitar")).toBeTruthy();
    expect(screen.getByText(/Mostrando resultados de/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Buscar «guitr» tal cual" }));
    expect(router.state.location.search).toBe("?q=guitr&exacta=1");
  });

  it("says what to try when nothing matches", async () => {
    shop({ "/api/busqueda/productos": () => jsonResponse({ consulta: "cuerdas", corregida: null, resultados: [] }) });
    renderAt("/?q=cuerdas&departamento=Guitars", routes);
    expect(await screen.findByText("Nada para «cuerdas»")).toBeTruthy();
    expect(screen.getByText(/Las fichas están en inglés/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Quitar filtros" }).length).toBeGreaterThan(0);
  });

  it("explains a malformed query with the api's kind", async () => {
    shop({
      "/api/busqueda/productos": () => jsonResponse({ error: "falta un operando", kind: "BooleanQueryError" }, { status: 400 }),
    });
    renderAt("/?q=%28guitar+AND", routes);
    expect(await screen.findByText("La búsqueda tiene un operador suelto")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it("starts with translated suggestions that search in english", async () => {
    shop({ "/api/busqueda/productos": () => jsonResponse({ consulta: "", corregida: null, resultados: [] }) });
    const user = userEvent.setup();
    const router = renderAt("/", routes);
    await user.click(await screen.findByRole("button", { name: /Baquetas/ }));
    expect(router.state.location.search).toBe("?q=drum+sticks&modo=palabras");
  });
});

describe("the shell", () => {
  it("says the shop is down and lets the user retry", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    renderAt("/", routes);
    expect(await screen.findByText("La tienda no está disponible")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeTruthy();
    expect(screen.getByText("Sin servicio")).toBeTruthy();
  });

  it("lets the user pick a customer, remembered in this browser", async () => {
    shop({ "/api/usuarios": () => jsonResponse([{ id: "AG3S4FROO422V5KP7DJCBXVUQLJQ", compras: 288 }]) });
    const user = userEvent.setup();
    renderAt("/para-ti", routes);
    await user.click(await screen.findByRole("button", { name: "Cliente 1 · 288 compras · AG3S4F…UQLJQ" }));
    expect(window.localStorage.getItem("cajon.cliente")).toBe("AG3S4FROO422V5KP7DJCBXVUQLJQ");
    expect((screen.getByLabelText("Cliente") as HTMLSelectElement).value).toBe("AG3S4FROO422V5KP7DJCBXVUQLJQ");
  });

  it("applies the chosen theme", async () => {
    shop({});
    const user = userEvent.setup();
    renderAt("/", routes);
    await user.click(await screen.findByRole("radio", { name: "Tema oscuro" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("answers an unknown path with a way back", async () => {
    shop({});
    renderAt("/no-existe", routes);
    expect(await screen.findByRole("heading", { name: "Esta dirección no existe" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ir al catálogo" })).toBeTruthy();
  });
});
