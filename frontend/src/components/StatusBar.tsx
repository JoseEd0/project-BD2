interface StatusBarProps {
  tableCount: number;
  rowCount: number | null;
  elapsedMs: number | null;
  sessionId: string;
  connected: boolean;
}

export default function StatusBar({
  tableCount,
  rowCount,
  elapsedMs,
  sessionId,
  connected,
}: StatusBarProps) {
  return (
    <footer className="statusbar">
      <span className="statusbar__item">
        Motor <span className="statusbar__value">{connected ? "conectado" : "sin conexión"}</span>
      </span>
      <span className="statusbar__item">
        Tablas <span className="statusbar__value">{tableCount}</span>
      </span>
      {rowCount !== null && (
        <span className="statusbar__item">
          Filas <span className="statusbar__value">{rowCount.toLocaleString("es")}</span>
        </span>
      )}
      {elapsedMs !== null && (
        <span className="statusbar__item">
          Tiempo <span className="statusbar__value">{elapsedMs.toFixed(2)} ms</span>
        </span>
      )}
      <span className="statusbar__spacer" />
      <span className="statusbar__item">
        Sesión <span className="statusbar__value">{sessionId}</span>
      </span>
    </footer>
  );
}
