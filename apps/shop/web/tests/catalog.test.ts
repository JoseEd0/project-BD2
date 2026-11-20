import { describe, expect, it } from "vitest";

import {
  activeFilterCount,
  filterQuery,
  filtersFromParams,
  isPriceRange,
  NO_FILTERS,
  PRICE_RANGES,
  priceRangeIsUpsideDown,
  withFilters,
} from "../src/catalog/filters";
import { toSearchMode } from "../src/catalog/searchMode";

describe("catalog filters", () => {
  it("read the department and the price range from the address", () => {
    const params = new URLSearchParams("q=pedal&departamento=Guitars&min=25&max=100");
    expect(filtersFromParams(params)).toEqual({ department: "Guitars", minPrice: 25, maxPrice: 100, minRating: null });
  });

  it("drop what is not a price", () => {
    expect(filtersFromParams(new URLSearchParams("min=-3&max=mucho&departamento="))).toEqual(NO_FILTERS);
  });

  it("write themselves back without touching the search", () => {
    const params = new URLSearchParams("q=pedal&modo=aspecto&min=5");
    const next = withFilters(params, { department: "Drums & Percussion", minPrice: null, maxPrice: 50, minRating: null });
    expect(next.toString()).toBe("q=pedal&modo=aspecto&departamento=Drums+%26+Percussion&max=50");
  });

  it("count a price range as one filter", () => {
    expect(activeFilterCount(NO_FILTERS)).toBe(0);
    expect(activeFilterCount({ department: "Guitars", minPrice: 10, maxPrice: 20, minRating: null })).toBe(2);
    expect(activeFilterCount({ department: null, minPrice: null, maxPrice: 20, minRating: null })).toBe(1);
  });

  it("recognise the one-click ranges", () => {
    const [cheap] = PRICE_RANGES;
    expect(cheap !== undefined && isPriceRange({ department: null, minPrice: null, maxPrice: 25, minRating: null }, cheap)).toBe(true);
  });

  it("spot an upside-down range before asking the api", () => {
    expect(priceRangeIsUpsideDown({ department: null, minPrice: 100, maxPrice: 10, minRating: null })).toBe(true);
    expect(priceRangeIsUpsideDown({ department: null, minPrice: 10, maxPrice: null, minRating: null })).toBe(false);
  });

  it("become the api's query parameters, leaving out what is not set", () => {
    expect(filterQuery({ department: "Guitars", minPrice: 0, maxPrice: null, minRating: null })).toEqual({ departamento: "Guitars", precio_min: 0 });
    expect(filterQuery(NO_FILTERS)).toEqual({});
  });
});

it("falls back to searching by words", () => {
  expect([toSearchMode("aspecto"), toSearchMode("foto"), toSearchMode(null)]).toEqual(["aspecto", "palabras", "palabras"]);
});
