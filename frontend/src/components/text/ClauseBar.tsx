import type { KeyboardEvent } from "react";

import type { Bm25Parameters } from "../../hooks/useLiveSearch";
import { steppedFromDefault } from "../../lib/textSearch";
import type { FilterDraft } from "../../lib/textSearch";
import type { ColumnInfo } from "../../types";

/** Cuántas filas puede pedir cada ranking. */
const LIMIT_OPTIONS = [5, 10, 20, 50];
const K1_STEP = 0.1;
const B_STEP = 0.05;
const B_MAX = 1;

interface ClauseBarProps {
  filters: FilterDraft[];
  filterColumns: ColumnInfo[];
  k1: string;
  b: string;
  /** Los del motor, para mostrarlos mientras no se escriban otros. */
  engine: Bm25Parameters;
  limit: number;
  problem: string | null;
  onAddFilter: () => void;
  onChangeFilter: (id: number, patch: Partial<FilterDraft>) => void;
  onRemoveFilter: (id: number) => void;
  onParameters: (patch: { k1?: string; b?: string }) => void;
  onLimit: (limit: number) => void;
}

function engineValue(value: number | null): string {
  return value === null ? "" : String(value);
}

/** Un parámetro de BM25. Vacío deja el del motor; ↑ y ↓ sobre el campo vacío parten de ese
 * valor y no de 0, que es lo que haría el navegador. */
function Parameter({
  name,
  value,
  engine,
  step,
  max,
  onChange,
}: {
  name: string;
  value: string;
  engine: number | null;
  step: number;
  max?: number;
  onChange: (value: string) => void;
}) {
  function stepFromEngine(event: KeyboardEvent<HTMLInputElement>) {
    const direction = event.key === "ArrowUp" ? 1 : event.key === "ArrowDown" ? -1 : 0;
    const stepped = steppedFromDefault(value, engine, step, direction);
    if (stepped !== null) {
      event.preventDefault();
      onChange(stepped);
    }
  }
  return (
    <label className="clause__parameter">
      {name} =
      <input
        className="clause__input clause__input--number"
        max={max}
        min={0}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={stepFromEngine}
        placeholder={engineValue(engine)}
        step={step}
        type="number"
        value={value}
      />
    </label>
  );
}

function Condition({
  filter,
  columns,
  onChange,
  onRemove,
}: {
  filter: FilterDraft;
  columns: ColumnInfo[];
  onChange: (patch: Partial<FilterDraft>) => void;
  onRemove: () => void;
}) {
  return (
    <span className="clause">
      <span className="clause__keyword">AND</span>
      <select
        aria-label="Columna de la condición"
        className="clause__input"
        onChange={(event) => onChange({ column: event.target.value })}
        value={filter.column}
      >
        {columns.map((column) => (
          <option key={column.name} value={column.name}>
            {column.name}
          </option>
        ))}
      </select>
      <span className="clause__operator">=</span>
      <input
        aria-label={`Valor de ${filter.column}`}
        className="clause__input clause__input--value"
        onChange={(event) => onChange({ value: event.target.value })}
        placeholder="valor"
        spellCheck={false}
        value={filter.value}
      />
      <button aria-label="Quitar la condición" className="clause__remove" onClick={onRemove} type="button">
        ×
      </button>
    </span>
  );
}

/**
 * Las cláusulas de la consulta, escritas como SQL: las condiciones `AND` que acompañan al
 * `MATCH`, el `WITH (k1, b)` de BM25 y el `LIMIT`. Cada cambio se ve enseguida en el SQL de
 * los dos rankings.
 */
export default function ClauseBar({
  filters,
  filterColumns,
  k1,
  b,
  engine,
  limit,
  problem,
  onAddFilter,
  onChangeFilter,
  onRemoveFilter,
  onParameters,
  onLimit,
}: ClauseBarProps) {
  const customized = k1.length > 0 || b.length > 0;
  return (
    <div className="clauses">
      <div aria-label="Cláusulas de la consulta" className="clauses__list" role="group">
        {filters.map((filter) => (
          <Condition
            columns={filterColumns}
            filter={filter}
            key={filter.id}
            onChange={(patch) => onChangeFilter(filter.id, patch)}
            onRemove={() => onRemoveFilter(filter.id)}
          />
        ))}
        {filterColumns.length > 0 && (
          <button className="clause clause--add" onClick={onAddFilter} type="button">
            <span className="clause__keyword">AND</span> condición
          </button>
        )}
        <span
          className="clause"
          title="Solo cambia el ranking de BM25. Vacío, usa el valor del motor; ↑ y ↓ lo ajustan."
        >
          <span className="clause__keyword">USING</span>
          <span className="clause__type">BM25</span>
          <span className="clause__keyword">WITH</span>
          <Parameter
            engine={engine.k1}
            name="k1"
            onChange={(next) => onParameters({ k1: next })}
            step={K1_STEP}
            value={k1}
          />
          <Parameter
            engine={engine.b}
            max={B_MAX}
            name="b"
            onChange={(next) => onParameters({ b: next })}
            step={B_STEP}
            value={b}
          />
          {customized && (
            <button
              aria-label="Volver a los k1 y b del motor"
              className="clause__remove"
              onClick={() => onParameters({ k1: "", b: "" })}
              type="button"
            >
              ×
            </button>
          )}
        </span>
        <label className="clause">
          <span className="clause__keyword">LIMIT</span>
          <select
            aria-label="Filas por ranking"
            className="clause__input"
            onChange={(event) => onLimit(Number(event.target.value))}
            value={limit}
          >
            {LIMIT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      </div>
      {problem !== null && <p className="clauses__problem">{problem}</p>}
    </div>
  );
}
