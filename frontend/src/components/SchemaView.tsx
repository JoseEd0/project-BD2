import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { inferRelations, layoutInLayers } from "../lib/relations";
import type { TableInfo } from "../types";
import { ORGANIZATION_LABEL, typeLabel } from "./FilesPanel";

interface SchemaViewProps {
  tables: TableInfo[];
  focused: string | null;
  onPreview: (table: TableInfo) => void;
}

interface Edge {
  id: string;
  path: string;
}

const CURVE_TENSION = 60;

export default function SchemaView({ tables, focused, onPreview }: SchemaViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const anchors = useRef(new Map<string, HTMLElement>());
  const cards = useRef(new Map<string, HTMLElement>());
  const [edges, setEdges] = useState<Edge[]>([]);
  const [canvas, setCanvas] = useState({ width: 0, height: 0 });

  const relations = useMemo(() => inferRelations(tables), [tables]);
  const columns = useMemo(() => layoutInLayers(tables, relations), [relations, tables]);

  const register = useCallback((key: string, element: HTMLElement | null) => {
    if (element === null) {
      anchors.current.delete(key);
    } else {
      anchors.current.set(key, element);
    }
  }, []);

  useEffect(() => {
    if (focused === null) return;
    cards.current.get(focused)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focused]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (container === null) {
      return;
    }
    const measure = () => {
      const base = container.getBoundingClientRect();
      const drawn: Edge[] = [];
      for (const relation of relations) {
        const from = anchors.current.get(`${relation.fromTable}.${relation.fromColumn}`);
        const to = anchors.current.get(`${relation.toTable}.${relation.toColumn}`);
        if (!from || !to) continue;
        const source = from.getBoundingClientRect();
        const target = to.getBoundingClientRect();
        const startsLeft = source.left <= target.left;
        const x1 = (startsLeft ? source.right : source.left) - base.left + container.scrollLeft;
        const y1 = source.top + source.height / 2 - base.top + container.scrollTop;
        const x2 = (startsLeft ? target.left : target.right) - base.left + container.scrollLeft;
        const y2 = target.top + target.height / 2 - base.top + container.scrollTop;
        const bend = startsLeft ? CURVE_TENSION : -CURVE_TENSION;
        drawn.push({
          id: `${relation.fromTable}.${relation.fromColumn}->${relation.toTable}`,
          path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
        });
      }
      setEdges(drawn);
      setCanvas({ width: container.scrollWidth, height: container.scrollHeight });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [relations, tables]);

  if (tables.length === 0) {
    return (
      <section className="panel panel--schema">
        <header className="panel__header">
          <h2 className="panel__title">Esquema</h2>
        </header>
        <p className="panel__empty">Crea alguna tabla para ver aquí su estructura.</p>
      </section>
    );
  }

  return (
    <section className="panel panel--schema">
      <header className="panel__header">
        <h2 className="panel__title">Esquema y relaciones</h2>
        <span className="panel__hint">
          {tables.length} tabla(s) · {relations.length} relación(es) inferida(s)
        </span>
      </header>
      <div className="schema" ref={containerRef}>
        <svg className="schema__edges" height={canvas.height} width={canvas.width}>
          <defs>
            <marker
              id="arrow"
              markerHeight="6"
              markerWidth="8"
              orient="auto"
              refX="7"
              refY="3"
            >
              <path d="M0,0 L8,3 L0,6 Z" fill="var(--key)" />
            </marker>
          </defs>
          {edges.map((edge) => (
            <path className="schema__edge" d={edge.path} key={edge.id} markerEnd="url(#arrow)" />
          ))}
        </svg>
        <div className="schema__cards">
          {columns.map((column, index) => (
            <div className="schema__column" key={index}>
          {column.map((table) => (
            <article
              className={table.name === focused ? "card card--focused" : "card"}
              key={table.name}
              ref={(element) => {
                if (element === null) {
                  cards.current.delete(table.name);
                } else {
                  cards.current.set(table.name, element);
                }
              }}
            >
              <header className="card__header">
                <h3 className="card__name">{table.name}</h3>
                <span className="chip chip--org">
                  {ORGANIZATION_LABEL[table.organization] ?? table.organization}
                </span>
                <button className="icon-button" onClick={() => onPreview(table)} type="button">
                  ▦
                </button>
              </header>
              <div className="card__stats">
                <span>{table.row_count.toLocaleString("es")} filas</span>
                <span>{table.columns.length} columnas</span>
                <span>{table.indexes.length} índice(s)</span>
              </div>
              <ul className="card__columns">
                {table.columns.map((column) => (
                  <li
                    className="card__column"
                    key={column.name}
                    ref={(element) => register(`${table.name}.${column.name}`, element)}
                  >
                    <span className="column__key">{column.primary_key ? "PK" : ""}</span>
                    <span className="column__name">{column.name}</span>
                    {column.indexed_with && (
                      <span className="column__index">{column.indexed_with}</span>
                    )}
                    <span className="column__type">{typeLabel(column)}</span>
                  </li>
                ))}
              </ul>
            </article>
          ))}
            </div>
          ))}
        </div>
      </div>
      <div className="schema__note">
        <p>
          Las flechas son <strong>relaciones deducidas por el nombre de la columna</strong>: el
          motor todavía no declara claves foráneas, así que son una lectura del esquema y no
          una restricción que la base de datos imponga.
        </p>
        {relations.length > 0 && (
          <ul className="schema__relations">
            {relations.map((relation) => (
              <li key={`${relation.fromTable}.${relation.fromColumn}`}>
                <code>
                  {relation.fromTable}.{relation.fromColumn}
                </code>
                {" → "}
                <code>
                  {relation.toTable}.{relation.toColumn}
                </code>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
