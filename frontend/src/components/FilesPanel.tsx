import { useState } from "react";

import type { ColumnInfo, TableInfo } from "../types";

interface FilesPanelProps {
  tables: TableInfo[];
  onPickTable: (table: TableInfo) => void;
}

const ORGANIZATION_LABEL: Record<string, string> = {
  heap: "heap file",
  sequential: "secuencial",
  clustered_btree: "B+ agrupado",
};

function typeLabel(column: ColumnInfo): string {
  return column.length === null ? column.type : `${column.type}(${column.length})`;
}

function ColumnRow({ column }: { column: ColumnInfo }) {
  return (
    <li className="column">
      <span className="column__key" title={column.primary_key ? "clave primaria" : undefined}>
        {column.primary_key ? "PK" : ""}
      </span>
      <span className="column__name">{column.name}</span>
      {column.indexed_with && <span className="column__index">{column.indexed_with}</span>}
      {!column.nullable && <span className="column__null">NOT NULL</span>}
      <span className="column__type">{typeLabel(column)}</span>
    </li>
  );
}

function TableGroup({ table, onPick }: { table: TableInfo; onPick: () => void }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="tables__group">
      <button className="table-head" onClick={() => setOpen(!open)} type="button">
        <span className="table-head__caret">{open ? "▾" : "▸"}</span>
        <span className="table-head__name">{table.name}</span>
        <span className="table-head__count">{table.row_count.toLocaleString("es")} filas</span>
      </button>
      {open && (
        <>
          <div className="table-meta">
            <span className="chip chip--org">
              {ORGANIZATION_LABEL[table.organization] ?? table.organization}
            </span>
            {table.indexes.map((name) => (
              <span className="chip" key={name}>
                {name}
              </span>
            ))}
            <button className="snippet" onClick={onPick} type="button">
              SELECT *
            </button>
          </div>
          <ul className="columns">
            {table.columns.map((column) => (
              <ColumnRow column={column} key={column.name} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export default function FilesPanel({ tables, onPickTable }: FilesPanelProps) {
  return (
    <section className="panel panel--sidebar">
      <header className="panel__header">
        <h2 className="panel__title">Archivos</h2>
        <span className="panel__hint">{tables.length} tabla(s)</span>
      </header>
      <div className="panel__body">
        {tables.length === 0 ? (
          <p className="panel__empty">
            Todavía no hay tablas. Crea una con <code>CREATE TABLE</code> desde el editor.
          </p>
        ) : (
          <div className="tables">
            {tables.map((table) => (
              <TableGroup key={table.name} onPick={() => onPickTable(table)} table={table} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
