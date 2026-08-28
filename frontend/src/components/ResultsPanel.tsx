import type { CellValue, QueryFailure, QueryResponse } from "../types";

/** Dígitos significativos que bastan para quitar el ruido binario de un doble. */
const SIGNIFICANT_DIGITS = 12;

function formatNumber(value: number): string {
  if (Number.isInteger(value)) {
    return value.toString();
  }
  return Number(value.toPrecision(SIGNIFICANT_DIGITS)).toString();
}

interface ResultsPanelProps {
  result: QueryResponse | null;
  failure: QueryFailure | null;
}

function renderCell(value: CellValue) {
  if (value === null) {
    return <span>NULL</span>;
  }
  if (typeof value === "boolean") {
    return <span>{value ? "true" : "false"}</span>;
  }
  if (typeof value === "number") {
    return <span>{formatNumber(value)}</span>;
  }
  return <span>{String(value)}</span>;
}

function cellClass(value: CellValue): string {
  if (value === null) {
    return "is-null";
  }
  return typeof value === "number" ? "is-number" : "";
}

function Failure({ failure }: { failure: QueryFailure }) {
  return (
    <div className="notice notice--error">
      <div className="notice__title">{failure.kind}</div>
      <div>{failure.error}</div>
      {failure.line !== null && (
        <div className="notice__where">
          línea {failure.line}, columna {failure.column}
        </div>
      )}
    </div>
  );
}

export default function ResultsPanel({ result, failure }: ResultsPanelProps) {
  const rowCount = result?.rows.length ?? 0;

  return (
    <section className="panel">
      <header className="panel__header">
        <h2 className="panel__title">Resultados</h2>
        {result !== null && failure === null && (
          <span className="panel__hint">
            {rowCount.toLocaleString("es")} fila(s) · {result.elapsed_ms.toFixed(2)} ms
          </span>
        )}
      </header>
      <div className="panel__body">
        {failure !== null && <Failure failure={failure} />}
        {failure === null && result === null && (
          <p className="panel__empty">Ejecuta una consulta para ver aquí sus filas.</p>
        )}
        {failure === null && result !== null && result.columns.length === 0 && (
          <div className="notice notice--ok">
            <div className="notice__title">Sentencia ejecutada</div>
            <div>{result.message}</div>
          </div>
        )}
        {failure === null && result !== null && result.columns.length > 0 && (
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
                    <td className={cellClass(value)} key={columnIndex}>
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
