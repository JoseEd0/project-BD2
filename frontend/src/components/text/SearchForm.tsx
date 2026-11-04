import type { FormEvent } from "react";

import { targetKey } from "../../lib/textSearch";
import type { SearchDraft, SearchTarget } from "../../lib/textSearch";
import type { ColumnInfo } from "../../types";

/** Cuántos resultados puede pedir cada ranking. */
const LIMIT_OPTIONS = [5, 10, 20, 50];
const K1_STEP = 0.1;
const B_STEP = 0.05;

/** Los `k1` y `b` que usa el motor cuando la consulta no los pide; `null` hasta saberlo. */
export interface EngineDefaults {
  k1: number | null;
  b: number | null;
}

interface SearchFormProps {
  draft: SearchDraft;
  targets: SearchTarget[];
  target: SearchTarget | null;
  filterColumns: ColumnInfo[];
  defaults: EngineDefaults;
  examples: string[];
  problem: string | null;
  running: boolean;
  onChange: (patch: Partial<SearchDraft>) => void;
  onSearch: () => void;
  onExample: (query: string) => void;
}

function placeholder(value: number | null): string {
  return value === null ? "del motor" : `${value} (motor)`;
}

/** La búsqueda: dónde, qué, con qué filtro y con qué parámetros de BM25. */
export default function SearchForm({
  draft,
  targets,
  target,
  filterColumns,
  defaults,
  examples,
  problem,
  running,
  onChange,
  onSearch,
  onExample,
}: SearchFormProps) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSearch();
  }

  return (
    // Sin la validación del navegador: con `step` un 1,25 sería «inválido» y el formulario
    // no se enviaría en silencio. Los rangos de k1 y b los valida el motor, y su error se ve
    // en la columna de BM25.
    <form className="searchform" noValidate onSubmit={submit}>
      <div className="searchform__row searchform__row--main">
        <label className="field searchform__target">
          <span className="field__label">Buscar en</span>
          <select
            className="field__input"
            onChange={(event) => onChange({ target: event.target.value })}
            value={target === null ? "" : targetKey(target)}
          >
            {targets.map((item) => (
              <option key={targetKey(item)} value={targetKey(item)}>
                {targetKey(item)}
              </option>
            ))}
          </select>
        </label>
        <label className="field searchform__query">
          <span className="field__label">Consulta</span>
          <input
            className="field__input"
            onChange={(event) => onChange({ query: event.target.value })}
            placeholder="auriculares inalámbricos bluetooth"
            type="search"
            value={draft.query}
          />
        </label>
        <button
          className="action action--primary searchform__submit"
          disabled={running || problem !== null}
          type="submit"
        >
          {running ? "Buscando…" : "Buscar con los dos"}
        </button>
      </div>

      <div className="searchform__examples">
        <span className="searchform__hint">Ejemplos:</span>
        {examples.map((example) => (
          <button
            className="snippet"
            disabled={running}
            key={example}
            onClick={() => onExample(example)}
            type="button"
          >
            {example}
          </button>
        ))}
      </div>

      <div className="searchform__row">
        <fieldset className="searchform__group">
          <legend className="field__label">Filtro relacional (vacío: sin filtro)</legend>
          <select
            aria-label="Columna del filtro"
            className="field__input"
            disabled={filterColumns.length === 0}
            onChange={(event) => onChange({ filterColumn: event.target.value })}
            value={draft.filterColumn ?? ""}
          >
            {filterColumns.map((column) => (
              <option key={column.name} value={column.name}>
                {column.name}
              </option>
            ))}
          </select>
          <span className="searchform__operator">=</span>
          <input
            aria-label="Valor del filtro"
            className="field__input"
            disabled={draft.filterColumn === null}
            onChange={(event) => onChange({ filterValue: event.target.value })}
            placeholder="PUMA"
            value={draft.filterValue}
          />
        </fieldset>
        <fieldset className="searchform__group">
          <legend className="field__label">Parámetros de BM25</legend>
          <label className="searchform__parameter">
            k1
            <input
              className="field__input"
              min={0}
              onChange={(event) => onChange({ k1: event.target.value })}
              placeholder={placeholder(defaults.k1)}
              step={K1_STEP}
              type="number"
              value={draft.k1}
            />
          </label>
          <label className="searchform__parameter">
            b
            <input
              className="field__input"
              max={1}
              min={0}
              onChange={(event) => onChange({ b: event.target.value })}
              placeholder={placeholder(defaults.b)}
              step={B_STEP}
              type="number"
              value={draft.b}
            />
          </label>
          <button
            className="action"
            disabled={draft.k1.length === 0 && draft.b.length === 0}
            onClick={() => onChange({ k1: "", b: "" })}
            type="button"
          >
            Los del motor
          </button>
        </fieldset>
        <label className="field searchform__limit">
          <span className="field__label">Resultados</span>
          <select
            className="field__input"
            onChange={(event) => onChange({ limit: Number(event.target.value) })}
            value={draft.limit}
          >
            {LIMIT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                top {option}
              </option>
            ))}
          </select>
        </label>
      </div>
      {problem !== null && draft.query.length > 0 && (
        <p className="searchform__problem">{problem}</p>
      )}
    </form>
  );
}
