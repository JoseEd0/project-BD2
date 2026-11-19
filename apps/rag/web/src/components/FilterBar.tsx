import { useId } from "react";

import type { ResearchFilters } from "../api/types";
import { formatCount } from "../format";
import { useFacets } from "../research/useRetrieval";

interface FilterBarProps {
  readonly filters: ResearchFilters;
  readonly onChange: (filters: ResearchFilters) => void;
}

const ANY_YEAR = "";

function toYear(value: string): number | null {
  return value === ANY_YEAR ? null : Number(value);
}

/** Categorías y años del corpus: lo que va al `WHERE` de cada búsqueda. */
export function FilterBar({ filters, onChange }: FilterBarProps) {
  const { data: facets, isError } = useFacets();
  const fromId = useId();
  const toId = useId();
  if (facets === undefined) {
    return isError ? <p className="filters filters--unavailable">Los filtros no están disponibles: el servicio no dio los años ni las categorías.</p> : null;
  }

  function toggleCategory(category: string, on: boolean) {
    const categories = on ? [...filters.categories, category] : filters.categories.filter((current) => current !== category);
    onChange({ ...filters, categories });
  }

  const years = facets.years.map((facet) => facet.year);
  return (
    <fieldset className="filters">
      <legend className="visually-hidden">Filtros de la búsqueda</legend>
      <div className="filters__group" role="group" aria-label="Categorías">
        {facets.categories.map((facet) => (
          <label key={facet.category} className="chip">
            <input
              type="checkbox"
              checked={filters.categories.includes(facet.category)}
              onChange={(event) => toggleCategory(facet.category, event.target.checked)}
            />
            <span>
              {facet.category} <span className="chip__count">{formatCount(facet.papers)}</span>
            </span>
          </label>
        ))}
      </div>
      <div className="filters__group filters__years">
        <label htmlFor={fromId}>Desde</label>
        <select id={fromId} value={filters.yearFrom ?? ANY_YEAR} onChange={(event) => onChange({ ...filters, yearFrom: toYear(event.target.value) })}>
          <option value={ANY_YEAR}>cualquier año</option>
          {years.map((year) => (
            <option key={year} value={year} disabled={filters.yearTo !== null && year > filters.yearTo}>
              {year}
            </option>
          ))}
        </select>
        <label htmlFor={toId}>hasta</label>
        <select id={toId} value={filters.yearTo ?? ANY_YEAR} onChange={(event) => onChange({ ...filters, yearTo: toYear(event.target.value) })}>
          <option value={ANY_YEAR}>cualquier año</option>
          {years.map((year) => (
            <option key={year} value={year} disabled={filters.yearFrom !== null && year < filters.yearFrom}>
              {year}
            </option>
          ))}
        </select>
      </div>
    </fieldset>
  );
}
