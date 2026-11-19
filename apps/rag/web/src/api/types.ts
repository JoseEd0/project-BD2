import type { components } from "./generated/rag";

type Schemas = components["schemas"];

export type Paper = Schemas["Paper"];
export type SearchHit = Schemas["SearchHit"];
export type SearchResponse = Schemas["SearchResponse"];
export type SearchMode = Schemas["SearchRequest"]["mode"] & string;
export type ExecutedQuery = Schemas["ExecutedQuery"];
export type Facets = Schemas["Facets"];
export type PageResponse = Schemas["PageResponse"];
export type Passage = Schemas["Passage"];

/** Los filtros tal como los arma la interfaz; `toRequestFilters` los pasa al contrato. */
export interface ResearchFilters {
  readonly yearFrom: number | null;
  readonly yearTo: number | null;
  readonly categories: readonly string[];
}

export const NO_FILTERS: ResearchFilters = { yearFrom: null, yearTo: null, categories: [] };

export function hasFilters(filters: ResearchFilters): boolean {
  return filters.yearFrom !== null || filters.yearTo !== null || filters.categories.length > 0;
}

/** `undefined` sin filtros: la petición queda igual que sin la interfaz de filtros. */
export function toRequestFilters(filters: ResearchFilters): Schemas["SearchFilters"] | undefined {
  if (!hasFilters(filters)) {
    return undefined;
  }
  return { year_from: filters.yearFrom, year_to: filters.yearTo, categories: [...filters.categories] };
}
