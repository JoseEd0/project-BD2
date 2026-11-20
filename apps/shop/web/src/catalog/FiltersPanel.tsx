import { useId, useState, type FormEvent } from "react";

import { useDepartments } from "../api/shop";
import { Icon } from "../components/Icon";
import { Stars } from "../components/Stars";
import { formatCount } from "../format";
import {
  activeFilterCount,
  isPriceRange,
  NO_FILTERS,
  PRICE_RANGES,
  priceRangeIsUpsideDown,
  RATING_STEPS,
  type CatalogFilters,
} from "./filters";

interface FiltersPanelProps {
  readonly filters: CatalogFilters;
  readonly onChange: (filters: CatalogFilters) => void;
}

function amount(value: string): number | null {
  const parsed = Number(value.replace(",", "."));
  return value.trim() === "" || !Number.isFinite(parsed) || parsed < 0 ? null : parsed;
}

const DEPARTMENT_SKELETON_ROWS = 6;

/** Departamento, precio y valoración: una columna en el escritorio, un desplegable en el celular. */
export function FiltersPanel({ filters, onChange }: FiltersPanelProps) {
  const departments = useDepartments();
  const [open, setOpen] = useState(false);
  const [minimum, setMinimum] = useState(filters.minPrice?.toString() ?? "");
  const [maximum, setMaximum] = useState(filters.maxPrice?.toString() ?? "");
  const id = useId();
  const active = activeFilterCount(filters);

  const applyPrices = (event: FormEvent) => {
    event.preventDefault();
    onChange({ ...filters, minPrice: amount(minimum), maxPrice: amount(maximum) });
  };
  const choosePrices = (min: number | null, max: number | null) => {
    setMinimum(min?.toString() ?? "");
    setMaximum(max?.toString() ?? "");
    onChange({ ...filters, minPrice: min, maxPrice: max });
  };

  return (
    <aside className="filters" aria-label="Filtros">
      <button
        type="button"
        className="filters-toggle button button-quiet"
        aria-expanded={open}
        aria-controls={`${id}-panel`}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="filter" size={18} />
        {active === 0 ? "Filtros" : `Filtros (${active})`}
      </button>
      <div id={`${id}-panel`} className="filters-panel" data-open={open}>
        <fieldset className="filter-group">
          <legend>Departamento</legend>
          {departments.isError ? (
            <p className="filter-problem">
              No pudimos cargar los departamentos.{" "}
              <button type="button" className="button button-link" onClick={() => void departments.refetch()}>
                Volver a cargar
              </button>
            </p>
          ) : departments.isPending ? (
            <ul className="filter-options" role="status" aria-label="Cargando departamentos">
              {Array.from({ length: DEPARTMENT_SKELETON_ROWS }, (_, index) => (
                <li key={index} className="filter-skeleton" />
              ))}
            </ul>
          ) : (
            <ul className="filter-options">
              <li>
                <label className="filter-option">
                  <input
                    type="radio"
                    name={`${id}-department`}
                    checked={filters.department === null}
                    onChange={() => onChange({ ...filters, department: null })}
                  />
                  <span>Todos</span>
                </label>
              </li>
              {departments.data.map((department) => (
                <li key={department.departamento}>
                  <label className="filter-option">
                    <input
                      type="radio"
                      name={`${id}-department`}
                      checked={filters.department === department.departamento}
                      onChange={() => onChange({ ...filters, department: department.departamento })}
                    />
                    <span>{department.departamento}</span>
                    <span className="filter-count">{formatCount(department.productos)}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>
        <fieldset className="filter-group">
          <legend>Precio</legend>
          <ul className="filter-options">
            <li>
              <label className="filter-option">
                <input
                  type="radio"
                  name={`${id}-price`}
                  checked={filters.minPrice === null && filters.maxPrice === null}
                  onChange={() => choosePrices(null, null)}
                />
                <span>Cualquiera</span>
              </label>
            </li>
            {PRICE_RANGES.map((range) => (
              <li key={range.label}>
                <label className="filter-option">
                  <input
                    type="radio"
                    name={`${id}-price`}
                    checked={isPriceRange(filters, range)}
                    onChange={() => choosePrices(range.min, range.max)}
                  />
                  <span>{range.label}</span>
                </label>
              </li>
            ))}
          </ul>
          <form className="price-inputs" onSubmit={applyPrices}>
            <label>
              <span>Desde US$</span>
              <input inputMode="decimal" value={minimum} onChange={(event) => setMinimum(event.target.value)} />
            </label>
            <label>
              <span>Hasta US$</span>
              <input inputMode="decimal" value={maximum} onChange={(event) => setMaximum(event.target.value)} />
            </label>
            <button type="submit" className="button button-quiet">
              Aplicar
            </button>
          </form>
          {priceRangeIsUpsideDown(filters) && (
            <p className="field-error" role="alert">
              El mínimo es mayor que el máximo.
            </p>
          )}
          <p className="filter-note">Un producto sin precio no pasa un filtro de precio.</p>
        </fieldset>
        <fieldset className="filter-group">
          <legend>Valoración</legend>
          <ul className="filter-options">
            <li>
              <label className="filter-option">
                <input
                  type="radio"
                  name={`${id}-rating`}
                  checked={filters.minRating === null}
                  onChange={() => onChange({ ...filters, minRating: null })}
                />
                <span>Cualquiera</span>
              </label>
            </li>
            {RATING_STEPS.map((step) => (
              <li key={step}>
                <label className="filter-option">
                  <input
                    type="radio"
                    name={`${id}-rating`}
                    checked={filters.minRating === step}
                    onChange={() => onChange({ ...filters, minRating: step })}
                  />
                  <Stars rating={step} count={null} />
                  <span>o más</span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
        {active > 0 && (
          <button
            type="button"
            className="button button-link"
            onClick={() => {
              setMinimum("");
              setMaximum("");
              onChange(NO_FILTERS);
            }}
          >
            Quitar filtros
          </button>
        )}
      </div>
    </aside>
  );
}
