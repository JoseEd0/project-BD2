import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StageTimings } from "../src/components/StageTimings";

describe("StageTimings", () => {
  it("lists each stage with its time and share, plus the unattributed rest", () => {
    render(<StageTimings serverTiming="parse;dur=1, exec;dur=2, total;dur=4" caption="Consulta" />);
    expect(screen.getByText("4,00 ms")).toBeTruthy();
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    // Intl separa el número del «%» con un espacio duro.
    expect(rows.map((row) => row.textContent?.replace(/\s/g, " "))).toEqual([
      "Leer la consulta1,00 ms25 %",
      "Ejecutar2,00 ms50 %",
      "Fuera de las etapas1,00 ms25 %",
    ]);
  });

  it("names an unknown stage by its description, then by its name", () => {
    render(<StageTimings serverTiming='rerank;dur=3;desc="Reordenar", embed;dur=1' caption="Consulta" />);
    expect(screen.getByRole("rowheader", { name: "Reordenar" })).toBeTruthy();
    expect(screen.getByRole("rowheader", { name: "embed" })).toBeTruthy();
  });

  it("shows a lone total without a breakdown", () => {
    render(<StageTimings serverTiming="total;dur=1.150" caption="Salud" />);
    expect(screen.getByText("1,15 ms")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says so when the header is missing or carries no durations", () => {
    const { rerender } = render(<StageTimings serverTiming={null} caption="Consulta" />);
    expect(screen.getByText("El servidor no informó cuánto tardó cada etapa.")).toBeTruthy();
    rerender(<StageTimings serverTiming="garbage;;;, ,=" caption="Consulta" />);
    expect(screen.getByText("El servidor no informó cuánto tardó cada etapa.")).toBeTruthy();
  });
});
