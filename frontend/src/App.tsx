import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  ConnectionError,
  closeSession,
  dropAllTables,
  fetchTables,
  runQuery,
  uploadCsv,
  uploadFileOnly,
} from "./api";
import ConfirmDialog from "./components/ConfirmDialog";
import FilesPanel from "./components/FilesPanel";
import PlanPanel from "./components/PlanPanel";
import QueryPanel from "./components/QueryPanel";
import ResultsPanel from "./components/ResultsPanel";
import SchemaView from "./components/SchemaView";
import StatusBar from "./components/StatusBar";
import StructureDialog from "./components/StructureDialog";
import UploadDialog from "./components/UploadDialog";
import type { UploadValues } from "./components/UploadDialog";
import { useSplitter } from "./hooks/useSplitter";
import { useTheme } from "./hooks/useTheme";
import { createFromFileSql } from "./lib/csvFiles";
import { SNIPPET_GROUPS } from "./snippets";
import type { QueryFailure, QueryResponse, TableInfo } from "./types";

type View = "consulta" | "esquema";

const PREVIEW_LIMIT = 100;
const HISTORY_LIMIT = 25;
const SIDEBAR = { initial: 300, min: 220, max: 520 };
const EDITOR = { initial: 240, min: 120, max: 620 };

/** Identificador de la sesión de esta pestaña. Es aleatorio de verdad: quien adivinara el de
 * otra pestaña podría confirmar o abortar su transacción. */
function newSessionId(): string {
  return `ui-${crypto.randomUUID()}`;
}

function connectionFailure(error: ConnectionError): QueryFailure {
  return {
    error: error.message,
    kind: "ConnectionError",
    line: null,
    column: null,
    in_transaction: null,
  };
}

export default function App() {
  const [sessionId] = useState(newSessionId);
  const [view, setView] = useState<View>("consulta");
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [connected, setConnected] = useState(false);
  const [sql, setSql] = useState("");
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
  const [inspected, setInspected] = useState<TableInfo | null>(null);
  const inFlight = useRef(false);

  const { theme, toggleTheme } = useTheme();
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
    const release = () => closeSession(sessionId);
    window.addEventListener("pagehide", release);
    return () => window.removeEventListener("pagehide", release);
  }, [sessionId]);

  /** Lo que sigue a una respuesta del API: el estado de la transacción viene de ella. */
  const showResponse = useCallback(
    async (response: QueryResponse) => {
      setResult(response);
      setInTransaction(response.in_transaction);
      setConnected(true);
      await refreshTables();
    },
    [refreshTables],
  );

  /** Un error, del motor o de la conexión. Uno del motor dice cómo quedó la transacción:
   * un interbloqueo o una espera agotada la abortan, y la interfaz no puede seguir
   * mostrándola abierta. */
  const showFailure = useCallback(
    async (error: unknown) => {
      setResult(null);
      if (error instanceof ApiError) {
        setFailure(error.failure);
        if (error.failure.in_transaction !== null) {
          setInTransaction(error.failure.in_transaction);
        }
        await refreshTables();
        return;
      }
      if (error instanceof ConnectionError) {
        setConnected(false);
        setFailure(connectionFailure(error));
        return;
      }
      throw error;
    },
    [refreshTables],
  );

  /** Las lecturas de fondo —la estructura, los puntos del mapa— van por la sesión: si se
   * rinden esperando un bloqueo, la transacción abierta queda abortada y la insignia tiene
   * que dejar de mostrarla. */
  const noteBackgroundFailure = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.failure.in_transaction !== null) {
      setInTransaction(error.failure.in_transaction);
    }
  }, []);

  /** Una petición de la sesión cada vez. El botón se deshabilita, pero ⌘↵ o la vista previa
   * llegan igual: una sentencia que espera un bloqueo se mandaría dos veces. Es una ref y
   * no estado porque dos pulsaciones seguidas llegan antes de que React vuelva a pintar. */
  const exclusively = useCallback(async (task: () => Promise<void>) => {
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    setRunning(true);
    try {
      await task();
    } finally {
      inFlight.current = false;
      setRunning(false);
    }
  }, []);

  const execute = useCallback(
    async (script: string) => {
      if (script.trim().length === 0) {
        return;
      }
      await exclusively(async () => {
        setFailure(null);
        try {
          await showResponse(await runQuery(script, sessionId));
          setHistory((previous) =>
            [script, ...previous.filter((item) => item !== script)].slice(0, HISTORY_LIMIT),
          );
        } catch (error) {
          await showFailure(error);
        }
      });
    },
    [exclusively, sessionId, showFailure, showResponse],
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
    await exclusively(async () => {
      setFailure(null);
      try {
        await showResponse(await dropAllTables(sessionId));
      } catch (error) {
        await showFailure(error);
      }
    });
  }, [exclusively, sessionId, showFailure, showResponse]);

  const upload = useCallback(
    async (values: UploadValues) => {
      await exclusively(async () => {
        setUploading(true);
        setFailure(null);
        try {
          if (values.mode === "fileOnly") {
            const stored = await uploadFileOnly(values.file, values.name);
            setSql(createFromFileSql(values.name, stored.path, stored.columns));
            setView("consulta");
            setResult(null);
            setShowUpload(false);
            return;
          }
          const response = await uploadCsv({ ...values, sessionId });
          setShowUpload(false);
          await showResponse(response);
        } catch (error) {
          setShowUpload(false);
          await showFailure(error);
        } finally {
          setUploading(false);
        }
      });
    },
    [exclusively, sessionId, showFailure, showResponse],
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
        <button
          aria-label={theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro"}
          aria-pressed={theme === "dark"}
          className="action action--icon"
          onClick={toggleTheme}
          title={theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro"}
          type="button"
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </header>

      <div className="workbench__body">
        <FilesPanel
          onDescribe={describe}
          onDrop={setPendingDrop}
          onInspect={setInspected}
          onEmpty={() => setConfirmEmpty(true)}
          onPreview={preview}
          onUpload={() => setShowUpload(true)}
          tables={tables}
        />
        <div
          aria-label="Ancho del panel de archivos"
          className={sidebar.dragging ? "splitter splitter--x is-dragging" : "splitter splitter--x"}
          {...sidebar.separator}
        />
        {view === "consulta" ? (
          <div className="workbench__main">
            <QueryPanel
              history={history}
              onChange={setSql}
              onRun={() => void execute(sql)}
              running={running}
              snippetGroups={SNIPPET_GROUPS}
              sql={sql}
            />
            <div
              aria-label="Alto del editor"
              className={
                editor.dragging ? "splitter splitter--y is-dragging" : "splitter splitter--y"
              }
              {...editor.separator}
            />
            <div className="workbench__output">
              <ResultsPanel
                failure={failure}
                onBackgroundFailure={noteBackgroundFailure}
                result={result}
                sessionId={sessionId}
                tables={tables}
                theme={theme}
              />
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
          busy={uploading || running}
          onClose={() => setShowUpload(false)}
          onSubmit={(values) => void upload(values)}
        />
      )}

      {inspected !== null && (
        <StructureDialog
          onClose={() => setInspected(null)}
          onFailure={noteBackgroundFailure}
          sessionId={sessionId}
          table={inspected}
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
        rowCount={result ? result.total_rows : null}
        tableCount={tables.length}
        totalRows={totalRows}
      />
    </div>
  );
}
