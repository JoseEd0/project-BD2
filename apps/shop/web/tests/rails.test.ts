import { describe, expect, it } from "vitest";

import { newlyRecommended, signalRails, stampsOf, weightPercent } from "../src/recommend/rails";
import { CABLE, PEDAL, recommendations, STRINGS } from "./fixtures";

describe("signal rails", () => {
  it("put each recommendation in the rail of every signal that proposed it", () => {
    const rails = signalRails(recommendations());
    const ids = Object.fromEntries(rails.map((rail) => [rail.signal, rail.items.map((item) => item.recommendation.producto.id)]));
    expect(ids).toEqual({ compras: [CABLE, PEDAL], visual: [], texto: [STRINGS], cercania: [STRINGS, CABLE] });
  });

  it("order a rail by what its own signal contributed, not by the fused rank", () => {
    const nearby = signalRails(recommendations()).find((rail) => rail.signal === "cercania");
    expect(nearby?.items.map((item) => [item.contribution, item.reason])).toEqual([
      [0.09, "en stock a 400 m en Miraflores"],
      [0.08, "en stock a 1,2 km en Centro"],
    ]);
  });

  it("keep the four signals in order, with the query that ran or none", () => {
    const rails = signalRails(recommendations());
    expect(rails.map((rail) => [rail.signal, rail.query?.candidatos ?? null, rail.weight])).toEqual([
      ["compras", 2, 0.4],
      ["visual", null, 0.25],
      ["texto", 9, 0.25],
      ["cercania", 2, 0.1],
    ]);
  });

  it("treat an empty answer as four empty rails", () => {
    const rails = signalRails(recommendations({ senales: [], recomendaciones: [] }));
    expect(rails.every((rail) => rail.items.length === 0 && rail.query === null)).toBe(true);
  });

  it("ignore a signal the interface does not know", () => {
    const response = recommendations();
    const first = response.recomendaciones[0];
    if (first === undefined) {
      throw new Error("fixture without recommendations");
    }
    first.motivos.push({ senal: "otra", aporte: 1, texto: "?" });
    expect(stampsOf(first)).toEqual(["compras", "cercania"]);
  });
});

describe("stamps", () => {
  it("go from the signal that contributed most to the least", () => {
    const strings = recommendations().recomendaciones[2];
    expect(strings === undefined ? [] : stampsOf(strings)).toEqual(["texto", "cercania"]);
  });
});

describe("what a purchase changed", () => {
  it("is what is recommended now and was not before", () => {
    const before = recommendations();
    const after = recommendations({ recomendaciones: before.recomendaciones.slice(1).concat(before.recomendaciones[0] ?? []) });
    expect(newlyRecommended(before, after)).toEqual(new Set());
    const fresh = recommendations({ recomendaciones: before.recomendaciones.slice(0, 1) });
    expect([...newlyRecommended(fresh, before)].sort()).toEqual([PEDAL, STRINGS].sort());
  });

  it("is nothing while either side is missing", () => {
    expect(newlyRecommended(undefined, recommendations()).size).toBe(0);
  });
});

it("says weights as whole percentages", () => {
  expect([0.4, 0.25, 0.1, 0].map(weightPercent)).toEqual([40, 25, 10, 0]);
});
