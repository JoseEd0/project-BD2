import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, closeSession, dropAllTables, fetchTables, runQuery, uploadCsv } from "./api";
import ConfirmDialog from "./components/ConfirmDialog";
import FilesPanel from "./components/FilesPanel";
import PlanPanel from "./components/PlanPanel";
import QueryPanel from "./components/QueryPanel";
import ResultsPanel from "./components/ResultsPanel";
import SchemaView from "./components/SchemaView";
import StatusBar from "./components/StatusBar";
import UploadDialog from "./components/UploadDialog";
import type { UploadValues } from "./components/UploadDialog";
import { useSplitter } from "./hooks/useSplitter";
import { SNIPPETS } from "./snippets";
import type { QueryFailure, QueryResponse, TableInfo } from "./types";

type View = "consulta" | "esquema";

const PREVIEW_LIMIT = 100;
const HISTORY_LIMIT = 25;
const SIDEBAR = { initial: 300, min: 220, max: 520 };
const EDITOR = { initial: 240, min: 120, max: 620 };

function newSessionId(): string {
  return `ui-${Math.random().toString(36).slice(2, 8)}`;
}

export default function App() {
  const [sessionId] = useState(newSessionId);
  const [view, setView] = useState<View>("consulta");
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [connected, setConnected] = useState(false);
  const [sql, setSql] = useState(SNIPPETS[0].sql);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [failure, setFailure] = useState<QueryFailure | null>(null);
  const [inTransaction, setInTransaction] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [focusedTable, setFocusedTable] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [pendingDrop, setPendingDrop] = useState<TableInfo | null>(null);
  const [confirmEmpty, setConfirmEmpty] = useState(false);

  const sidebar = useSplitter(SIDEBAR.initial, SIDEBAR.min, SIDEBAR.max, "x");
  const editor = useSplitter(EDITOR.initial, EDITOR.min, EDITOR.max, "y");

  const totalRows = useMemo(
    () => tables.reduce((sum, table) => sum + table.row_count, 0),
    [tables],
  );

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

  const execute = useCallback(
    async (script: string) => {
      if (script.trim().length === 0) {
        return;
      }
      setRunning(true);
      setFailure(null);
      try {
        const response = await runQuery(script, sessionId);
        setResult(response);
        setInTransaction(response.in_transaction);
        setConnected(true);
        setHistory((previous) =>
          [script, ...previous.filter((item) => item !== script)].slice(0, HISTORY_LIMIT),
        );
        await refreshTables();
      } catch (error) {
        setResult(null);
        if (error instanceof ApiError) {
          setFailure(error.failure);
          await refreshTables();
        } else {
          setConnected(false);
          setFailure({
            error: "No se pudo contactar con el motor. ¿Está el API en marcha?",
            kind: "ConnectionError",
            line: null,
            column: null,
            statement_index: null,
          });
        }
      } finally {
        setRunning(false);
      }
    },
    [refreshTables, sessionId],
  );

  const preview = useCallback(
    (table: TableInfo) => {
      const query = `SELECT * FROM ${table.name} LIMIT ${PREVIEW_LIMIT};`;
      setView("consulta");
      setSql(query);
      void execute(query);
    },
    [execute],
  );

  const dropTable = useCallback(
    (table: TableInfo) => {
      setPendingDrop(null);
      void execute(`DROP TABLE ${table.name};`);
    },
    [execute],
  );

  const emptyDatabase = useCallback(async () => {
    setConfirmEmpty(false);
    setFailure(null);
    try {
      setResult(await dropAllTables(sessionId));
      await refreshTables();
    } catch (error) {
      if (error instanceof ApiError) {
        setFailure(error.failure);
        setResult(null);
      }
    }
  }, [refreshTables, sessionId]);

  const upload = useCallback(
    async (values: UploadValues) => {
      setUploading(true);
      setFailure(null);
      try {
        const response = await uploadCsv({ ...values, sessionId });
        setResult(response);
        setShowUpload(false);
        await refreshTables();
      } catch (error) {
        if (error instanceof ApiError) {
          setFailure(error.failure);
          setResult(null);
          setShowUpload(false);
        }
      } finally {
        setUploading(false);
      }
    },
    [refreshTables, sessionId],
  );

  const describe = useCallback((table: TableInfo) => {
    setFocusedTable(table.name);
    setView("esquema");
  }, []);

  return (
    <div
      className="workbench"
      style={
        {
          "--sidebar-width": `${sidebar.size}px`,
          "--editor-height": `${editor.size}px`,
        } as React.CSSProperties
      }
    >
      <header className="topbar">
        <div className="topbar__brand">
          Gestor de Base de Datos
        </div>
        <nav className="viewtabs">
          <button
            className={view === "consulta" ? "viewtab viewtab--active" : "viewtab"}
            onClick={() => setView("consulta")}
            type="button"
          >
            Consulta
          </button>
          <button
            className={view === "esquema" ? "viewtab viewtab--active" : "viewtab"}
            onClick={() => setView("esquema")}
            type="button"
          >
            Esquema
          </button>
        </nav>
        <span className="topbar__spacer" />
        <span className={inTransaction ? "badge badge--active" : "badge"}>
          <span className="badge__dot" />
          {inTransaction ? "Transacción abierta" : "Autocommit"}
        </span>
        <button className="action" onClick={() => void refreshTables()} type="button">
          Actualizar
        </button>
      </header>

      <div className="workbench__body">
        <FilesPanel
          onDescribe={describe}
          onDrop={setPendingDrop}
          onEmpty={() => setConfirmEmpty(true)}
          onPreview={preview}
          onUpload={() => setShowUpload(true)}
          tables={tables}
        />
        <div
          className={sidebar.dragging ? "splitter splitter--x is-dragging" : "splitter splitter--x"}
          onPointerDown={sidebar.onPointerDown}
          role="separator"
        />
        {view === "consulta" ? (
          <div className="workbench__main">
            <QueryPanel
              history={history}
              onChange={setSql}
              onRun={() => void execute(sql)}
              running={running}
              snippets={SNIPPETS}
              sql={sql}
            />
            <div
              className={
                editor.dragging ? "splitter splitter--y is-dragging" : "splitter splitter--y"
              }
              onPointerDown={editor.onPointerDown}
              role="separator"
            />
            <div className="workbench__output">
              <ResultsPanel failure={failure} result={result} />
              <PlanPanel plan={result?.plan ?? null} />
            </div>
          </div>
        ) : (
          <div className="workbench__main workbench__main--single">
            <SchemaView focused={focusedTable} onPreview={preview} tables={tables} />
          </div>
        )}
      </div>

      {showUpload && (
        <UploadDialog
          busy={uploading}
          onClose={() => setShowUpload(false)}
          onSubmit={(values) => void upload(values)}
        />
      )}

      {confirmEmpty && (
        <ConfirmDialog
          confirmLabel="Vaciar la base de datos"
          detail={`Se eliminarán las ${tables.length} tabla(s) y sus ${totalRows.toLocaleString("es")} fila(s), con sus índices y sus archivos en disco. No hay deshacer.`}
          message="¿Eliminar todas las tablas?"
          onClose={() => setConfirmEmpty(false)}
          onConfirm={() => void emptyDatabase()}
          title="Vaciar la base de datos"
        />
      )}

      {pendingDrop !== null && (
        <ConfirmDialog
          confirmLabel="Eliminar tabla"
          detail={`Se borrarán ${pendingDrop.row_count.toLocaleString("es")} fila(s), su archivo de datos y sus ${pendingDrop.indexes.length} índice(s). No hay deshacer.`}
          message={`¿Eliminar la tabla «${pendingDrop.name}»?`}
          onClose={() => setPendingDrop(null)}
          onConfirm={() => dropTable(pendingDrop)}
          title="Eliminar tabla"
        />
      )}

      <StatusBar
        connected={connected}
        elapsedMs={result?.elapsed_ms ?? null}
        rowCount={result ? result.rows.length : null}
        tableCount={tables.length}
        totalRows={totalRows}
      />
    </div>
  );
}
