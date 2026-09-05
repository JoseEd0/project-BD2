import { useState } from "react";

import { downloadCsv, toClipboardTable, toCsv } from "../lib/exporting";
import type { CellValue, QueryFailure, QueryResponse } from "../types";

interface ResultsPanelProps {
  result: QueryResponse | null;
  failure: QueryFailure | null;
}

type Tab = "filas" | "mensajes";

/** Dígitos significativos que bastan para quitar el ruido binario de un doble. */
const SIGNIFICANT_DIGITS = 12;

function formatNumber(value: number): string {
  if (Number.isInteger(value)) {
    return value.toString();
  }
  return Number(value.toPrecision(SIGNIFICANT_DIGITS)).toString();
}

function renderCell(value: CellValue) {
  if (value === null) return "NULL";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return formatNumber(value);
  return String(value);
}

function cellClass(value: CellValue): string {
  if (value === null) return "is-null";
  return typeof value === "number" ? "is-number" : "";
}

function Failure({ failure }: { failure: QueryFailure }) {
  const [headline, ...excerpt] = failure.error.split("\n");
  return (
    <div className="notice notice--error">
      <div className="notice__title">{failure.kind}</div>
      <div>{headline}</div>
      {excerpt.length > 0 && <pre className="notice__excerpt">{excerpt.join("\n")}</pre>}
      {excerpt.length === 0 && failure.line !== null && (
        <div className="notice__where">
          línea {failure.line}, columna {failure.column}
        </div>
      )}
    </div>
  );
}

function Messages({ result }: { result: QueryResponse }) {
  if (result.statements.length === 0) {
    return <p className="panel__empty">Sin mensajes.</p>;
  }
  return (
    <table className="grid">
      <thead>
        <tr>
          <th className="grid__index">#</th>
          <th>Sentencia</th>
          <th>Resultado</th>
          <th>Filas</th>
          <th>Tiempo</th>
        </tr>
      </thead>
      <tbody>
        {result.statements.map((item, index) => (
          <tr key={index}>
            <td className="grid__index">{index + 1}</td>
            <td>{item.sql}</td>
            <td>{item.message || `${item.returned_rows} fila(s) devueltas`}</td>
            <td className="is-number">{item.affected_rows}</td>
            <td className="is-number">{item.elapsed_ms.toFixed(2)} ms</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ResultsPanel({ result, failure }: ResultsPanelProps) {
  const [tab, setTab] = useState<Tab>("filas");
  const [copied, setCopied] = useState(false);
  const hasRows = result !== null && result.columns.length > 0;

  async function copyRows() {
    if (!result) return;
    await navigator.clipboard.writeText(toClipboardTable(result.columns, result.rows));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <section className="panel">
      <header className="panel__header">
        <h2 className="panel__title">Resultados</h2>
        <div className="tabs">
          <button
            className={tab === "filas" ? "tab tab--active" : "tab"}
            onClick={() => setTab("filas")}
            type="button"
          >
            Filas
            {hasRows && <span className="tab__count">{result.rows.length}</span>}
          </button>
          <button
            className={tab === "mensajes" ? "tab tab--active" : "tab"}
            onClick={() => setTab("mensajes")}
            type="button"
          >
            Mensajes
            {result !== null && result.statements.length > 1 && (
              <span className="tab__count">{result.statements.length}</span>
            )}
          </button>
        </div>
        <div className="panel__actions">
          {hasRows && (
            <>
              <button className="action" onClick={() => void copyRows()} type="button">
                {copied ? "Copiado" : "Copiar"}
              </button>
              <button
                className="action"
                onClick={() => downloadCsv("resultado.csv", toCsv(result.columns, result.rows))}
                type="button"
              >
                CSV
              </button>
            </>
          )}
          {result !== null && failure === null && (
            <span className="panel__hint">{result.elapsed_ms.toFixed(2)} ms</span>
          )}
        </div>
      </header>
      <div className="panel__body">
        {failure !== null && <Failure failure={failure} />}
        {failure === null && result === null && (
          <p className="panel__empty">Ejecuta una consulta para ver aquí sus filas.</p>
        )}
        {failure === null && result !== null && tab === "mensajes" && <Messages result={result} />}
        {failure === null && result !== null && tab === "filas" && !hasRows && (
          <div className="notice notice--ok">
            <div className="notice__title">Sentencia ejecutada</div>
            <div>{result.message || "Sin filas que mostrar."}</div>
          </div>
        )}
        {failure === null && result !== null && tab === "filas" && hasRows && (
          <table className="grid">
            <thead>
              <tr>
                <th className="grid__index">#</th>
                {result.columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  <td className="grid__index">{rowIndex + 1}</td>
                  {row.map((value, columnIndex) => (
                    <td className={cellClass(value)} key={columnIndex} title={renderCell(value)}>
                      {renderCell(value)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
