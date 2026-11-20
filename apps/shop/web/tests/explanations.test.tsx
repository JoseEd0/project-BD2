import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RecommendationRails } from "../src/components/RecommendationRails";
import { CABLE, recommendations } from "./fixtures";
import { renderPiece } from "./render";

const MEASURED = { serverTiming: "cache;dur=1.5, compras;dur=4.5, gestor;dur=30, total;dur=40", cache: "miss" as const };

function rail(name: RegExp) {
  return screen.getByRole("region", { name });
}

describe("recommendation rails", () => {
  it("show the fused row with every stamp and the whole explanation", async () => {
    renderPiece(<RecommendationRails result={{ data: recommendations(), ...MEASURED }} scope="producto" fusedTitle="Lo que más va" />);
    const fused = rail(/Lo que más va/);
    const cable = within(fused).getAllByRole("link")[0];
    expect(cable?.getAttribute("href")).toBe(`/producto/${CABLE}`);
    expect(within(cable as HTMLElement).getByText("Comprado junto")).toBeTruthy();
    expect(within(cable as HTMLElement).getByText("En stock cerca")).toBeTruthy();
    expect(
      within(cable as HTMLElement).getByText("comprado junto con este producto por 12 clientes · en stock a 1,2 km en Centro"),
    ).toBeTruthy();
    expect(within(fused).getByText(/comprado junto 40\s%, se ve parecido 25\s%/)).toBeTruthy();
  });

  it("give each signal its own row with only its own sentence", () => {
    renderPiece(<RecommendationRails result={{ data: recommendations(), ...MEASURED }} scope="producto" fusedTitle="Lo que más va" />);
    const nearby = rail(/En stock cerca de ti/);
    expect(within(nearby).getAllByRole("link")).toHaveLength(2);
    expect(within(nearby).getByText("en stock a 400 m en Miraflores")).toBeTruthy();
    expect(within(nearby).queryByText(/comprado junto con este producto/)).toBeNull();
  });

  it("explain why a signal row is empty", () => {
    renderPiece(<RecommendationRails result={{ data: recommendations(), ...MEASURED }} scope="producto" fusedTitle="Lo que más va" />);
    const visual = rail(/Se ve parecido/);
    expect(within(visual).getByText(/no tiene foto/)).toBeTruthy();
    expect(within(visual).getByText(/no corrió/)).toBeTruthy();
  });

  it("say that a signal with candidates weighs nothing instead of blaming the photo", () => {
    const base = recommendations();
    const visualQuery = { senal: "visual", sql: "SELECT id FROM shop_fotos", parametros: [], candidatos: 61, milisegundos: 63.8 };
    const unweighted = recommendations({ pesos: { ...base.pesos, visual: 0 }, senales: [...base.senales, visualQuery] });
    renderPiece(<RecommendationRails result={{ data: unweighted, ...MEASURED }} scope="producto" fusedTitle="Lo que más va" />);
    const visual = rail(/Se ve parecido/);
    expect(within(visual).getByText(/pesa 0\s% en la fusión/)).toBeTruthy();
    expect(within(visual).queryByText(/no tiene foto/)).toBeNull();
  });

  it("show the exact sql of a signal with its parameters on request", async () => {
    const user = userEvent.setup();
    renderPiece(<RecommendationRails result={{ data: recommendations(), ...MEASURED }} scope="producto" fusedTitle="Lo que más va" />);
    const bought = rail(/Comprado junto con este producto/);
    await user.click(within(bought).getByText("Ver SQL"));
    expect(within(bought).getByText(/FROM shop_compras_conjuntas/)).toBeTruthy();
    expect(within(bought).getByText("'B000GUITAR'")).toBeTruthy();
    expect(within(bought).getByText("2 candidatos en 4.5 ms")).toBeTruthy();
  });

  it("explain a cold start and say where the time went and the cache", () => {
    const cold = recommendations({ arranque_en_frio: true, pesos: { compras: 0, visual: 0.45, texto: 0.45, cercania: 0.1 } });
    renderPiece(<RecommendationRails result={{ data: cold, ...MEASURED }} scope="producto" fusedTitle="Lo que más va" />);
    expect(screen.getByRole("note").textContent).toMatch(/Arranque en frío/);
    expect(screen.getByText("caché: fallo")).toBeTruthy();
    expect(screen.getByText(/Tiempos · 40 ms/)).toBeTruthy();
  });

  it("mark what is new after a purchase", () => {
    renderPiece(
      <RecommendationRails
        result={{ data: recommendations(), ...MEASURED }}
        scope="usuario"
        fusedTitle="Lo que más va contigo"
        newIds={new Set([CABLE])}
      />,
    );
    expect(within(rail(/Lo que más va contigo/)).getAllByText("Nuevo")).toHaveLength(1);
  });

  it("say why there is nothing to recommend", () => {
    const empty = recommendations({ senales: [], recomendaciones: [] });
    renderPiece(<RecommendationRails result={{ data: empty, ...MEASURED }} scope="usuario" fusedTitle="Lo que más va contigo" />);
    expect(screen.getByText(/no tiene compras con que recomendarle/)).toBeTruthy();
  });
});
