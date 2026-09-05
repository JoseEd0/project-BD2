interface StatusBarProps {
  tableCount: number;
  totalRows: number;
  rowCount: number | null;
  elapsedMs: number | null;
  connected: boolean;
}

export default function StatusBar({
  tableCount,
  totalRows,
  rowCount,
  elapsedMs,
  connected,
}: StatusBarProps) {
  return (
    <footer className="statusbar">
      <span className={connected ? "statusbar__item" : "statusbar__item statusbar__item--down"}>
        <span className="badge__dot" />
        {connected ? "Motor conectado" : "Sin conexión con el motor"}
      </span>
      <span className="statusbar__item">
        Tablas <span className="statusbar__value">{tableCount}</span>
      </span>
      <span className="statusbar__item">
        Filas almacenadas <span className="statusbar__value">{totalRows.toLocaleString("es")}</span>
      </span>
      <span className="statusbar__spacer" />
      {rowCount !== null && (
        <span className="statusbar__item">
          Devueltas <span className="statusbar__value">{rowCount.toLocaleString("es")}</span>
        </span>
      )}
      {elapsedMs !== null && (
        <span className="statusbar__item">
          Tiempo <span className="statusbar__value">{elapsedMs.toFixed(2)} ms</span>
        </span>
      )}
    </footer>
  );
}
