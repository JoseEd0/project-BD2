import { useCallback, useEffect, useState } from "react";

import { ApiError, closeSession, fetchTables, runQuery } from "./api";
import FilesPanel from "./components/FilesPanel";
import PlanPanel from "./components/PlanPanel";
import QueryPanel from "./components/QueryPanel";
import ResultsPanel from "./components/ResultsPanel";
import StatusBar from "./components/StatusBar";
import { SNIPPETS } from "./snippets";
import type { QueryFailure, QueryResponse, TableInfo } from "./types";

const PREVIEW_LIMIT = 50;

function newSessionId(): string {
  return `ui-${Math.random().toString(36).slice(2, 8)}`;
}

export default function App() {
  const [sessionId] = useState(newSessionId);
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [connected, setConnected] = useState(false);
  const [sql, setSql] = useState(SNIPPETS[0].sql);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [failure, setFailure] = useState<QueryFailure | null>(null);
  const [inTransaction, setInTransaction] = useState(false);

  const refreshTables = useCallback(async () => {
    try {
      setTables(await fetchTables());
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    void refreshTables();
  }, [refreshTables]);

  useEffect(() => {
    const release = () => {
      void closeSession(sessionId);
    };
    window.addEventListener("beforeunload", release);
    return () => window.removeEventListener("beforeunload", release);
  }, [sessionId]);

  const execute = useCallback(async () => {
    if (sql.trim().length === 0) {
      return;
    }
    setRunning(true);
    setFailure(null);
    try {
      const response = await runQuery(sql, sessionId);
      setResult(response);
      setInTransaction(response.in_transaction);
      setConnected(true);
      await refreshTables();
    } catch (error) {
      setResult(null);
      if (error instanceof ApiError) {
        setFailure(error.failure);
      } else {
        setConnected(false);
        setFailure({
          error: "No se pudo contactar con el motor. ¿Está el API en marcha?",
          kind: "ConnectionError",
          line: null,
          column: null,
        });
      }
    } finally {
      setRunning(false);
    }
  }, [refreshTables, sessionId, sql]);

  return (
    <div className="workbench">
      <header className="topbar">
        <div className="topbar__brand">
          Minigestor <span>Base de Datos 2 · Parte 1 relacional</span>
        </div>
        <span className="topbar__spacer" />
        <span className={inTransaction ? "badge badge--active" : "badge"}>
          <span className="badge__dot" />
          {inTransaction ? "Transacción abierta" : "Autocommit"}
        </span>
      </header>

      <div className="workbench__body">
        <FilesPanel
          onPickTable={(table) =>
            setSql(`SELECT * FROM ${table.name} LIMIT ${PREVIEW_LIMIT};`)
          }
          tables={tables}
        />
        <div className="workbench__main">
          <QueryPanel
            onChange={setSql}
            onPickSnippet={setSql}
            onRun={() => void execute()}
            running={running}
            snippets={SNIPPETS}
            sql={sql}
          />
          <div className="workbench__output">
            <ResultsPanel failure={failure} result={result} />
            <PlanPanel plan={result?.plan ?? null} />
          </div>
        </div>
      </div>

      <StatusBar
        connected={connected}
        elapsedMs={result?.elapsed_ms ?? null}
        rowCount={result ? result.rows.length : null}
        sessionId={sessionId}
        tableCount={tables.length}
      />
    </div>
  );
}
