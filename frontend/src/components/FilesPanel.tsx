import { useMemo, useState } from "react";

import type { ColumnInfo, TableInfo } from "../types";

interface FilesPanelProps {
  tables: TableInfo[];
  onPreview: (table: TableInfo) => void;
  onDescribe: (table: TableInfo) => void;
  onDrop: (table: TableInfo) => void;
  onUpload: () => void;
  onEmpty: () => void;
}

export const ORGANIZATION_LABEL: Record<string, string> = {
  heap: "heap file",
  sequential: "secuencial",
  clustered_btree: "B+ agrupado",
};

export function typeLabel(column: ColumnInfo): string {
  return column.length === null ? column.type : `${column.type}(${column.length})`;
}

function ColumnRow({ column }: { column: ColumnInfo }) {
  return (
    <li className="column">
      <span className="column__key">{column.primary_key ? "PK" : ""}</span>
      <span className="column__name">{column.name}</span>
      {column.indexed_with && <span className="column__index">{column.indexed_with}</span>}
      {!column.nullable && <span className="column__null">NOT NULL</span>}
      <span className="column__type">{typeLabel(column)}</span>
    </li>
  );
}

function TableGroup({
  table,
  onPreview,
  onDescribe,
  onDrop,
}: {
  table: TableInfo;
  onPreview: () => void;
  onDescribe: () => void;
  onDrop: () => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="tables__group">
      <div className="table-head">
        <button
          className="table-head__toggle"
          onClick={() => setOpen(!open)}
          title={open ? "Contraer" : "Expandir"}
          type="button"
        >
          <span className="table-head__caret">{open ? "▾" : "▸"}</span>
          <span className="table-head__name">{table.name}</span>
        </button>
        <span className="table-head__count">{table.row_count.toLocaleString("es")}</span>
        <button className="icon-button" onClick={onPreview} title="Ver datos" type="button">
          ▦
        </button>
        <button className="icon-button" onClick={onDescribe} title="Ver estructura" type="button">
          ⓘ
        </button>
        <button
          className="icon-button icon-button--danger"
          onClick={onDrop}
          title="Eliminar tabla"
          type="button"
        >
          🗑
        </button>
      </div>
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

export default function FilesPanel({
  tables,
  onPreview,
  onDescribe,
  onDrop,
  onUpload,
  onEmpty,
}: FilesPanelProps) {
  const [filter, setFilter] = useState("");
  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (needle.length === 0) return tables;
    return tables.filter(
      (table) =>
        table.name.toLowerCase().includes(needle) ||
        table.columns.some((column) => column.name.toLowerCase().includes(needle)),
    );
  }, [filter, tables]);

  return (
    <section className="panel panel--sidebar">
      <header className="panel__header">
        <h2 className="panel__title">Archivos</h2>
        <span className="panel__hint">{tables.length}</span>
        <div className="panel__actions">
          <button className="action" onClick={onUpload} type="button">
            Cargar CSV
          </button>
          <button
            className="icon-button icon-button--danger"
            disabled={tables.length === 0}
            onClick={onEmpty}
            title="Vaciar la base de datos"
            type="button"
          >
            ⌦
          </button>
        </div>
      </header>
      <div className="search">
        <input
          className="search__input"
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filtrar tablas y columnas…"
          value={filter}
        />
      </div>
      <div className="panel__body">
        {tables.length === 0 && (
          <p className="panel__empty">
            Todavía no hay tablas. Crea una con <code>CREATE TABLE</code> desde el editor.
          </p>
        )}
        {tables.length > 0 && visible.length === 0 && (
          <p className="panel__empty">Nada coincide con «{filter}».</p>
        )}
        {visible.length > 0 && (
          <div className="tables">
            {visible.map((table) => (
              <TableGroup
                key={table.name}
                onDescribe={() => onDescribe(table)}
                onDrop={() => onDrop(table)}
                onPreview={() => onPreview(table)}
                table={table}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
