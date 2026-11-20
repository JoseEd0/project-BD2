import { jsonResponse, stubFetch } from "@minigestor/web-kit/testing";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { FiltersPanel } from "../src/catalog/FiltersPanel";
import { NO_FILTERS } from "../src/catalog/filters";
import { renderPiece } from "./render";

function departments() {
  stubFetch((url) =>
    url.pathname === "/api/departamentos"
      ? jsonResponse([
          { departamento: "Instrument Accessories", productos: 11519 },
          { departamento: "Guitars", productos: 1066 },
        ])
      : jsonResponse({}, { status: 404 }),
  );
}

it("chooses a department with its product count", async () => {
  departments();
  const onChange = vi.fn();
  const user = userEvent.setup();
  renderPiece(<FiltersPanel filters={NO_FILTERS} onChange={onChange} />);
  await user.click(await screen.findByRole("radio", { name: /Guitars/ }));
  expect(onChange).toHaveBeenLastCalledWith({ department: "Guitars", minPrice: null, maxPrice: null, minRating: null });
  expect(screen.getByText("11,519")).toBeTruthy();
});

it("chooses a price range in one click or by typing it", async () => {
  departments();
  const onChange = vi.fn();
  const user = userEvent.setup();
  renderPiece(<FiltersPanel filters={NO_FILTERS} onChange={onChange} />);
  await user.click(screen.getByRole("radio", { name: "US$ 25 a 100" }));
  expect(onChange).toHaveBeenLastCalledWith({ department: null, minPrice: 25, maxPrice: 100, minRating: null });
  await user.clear(screen.getByLabelText("Desde US$"));
  await user.type(screen.getByLabelText("Desde US$"), "12,5");
  await user.clear(screen.getByLabelText("Hasta US$"));
  await user.click(screen.getByRole("button", { name: "Aplicar" }));
  expect(onChange).toHaveBeenLastCalledWith({ department: null, minPrice: 12.5, maxPrice: null, minRating: null });
});

it("warns about an upside-down range and clears every filter", async () => {
  departments();
  const onChange = vi.fn();
  const user = userEvent.setup();
  renderPiece(<FiltersPanel filters={{ department: "Guitars", minPrice: 100, maxPrice: 10, minRating: null }} onChange={onChange} />);
  expect(screen.getByRole("alert").textContent).toBe("El mínimo es mayor que el máximo.");
  expect(screen.getByRole("button", { name: "Filtros (2)" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Quitar filtros" }));
  expect(onChange).toHaveBeenLastCalledWith(NO_FILTERS);
});
