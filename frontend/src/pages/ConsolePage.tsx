import { useCallback, useState } from "react";

import { dropAllTables, runQuery, uploadCsv, uploadFileOnly } from "../api";
import ConfirmDialog from "../components/ConfirmDialog";
import FilesPanel from "../components/FilesPanel";
import PlanPanel from "../components/PlanPanel";
import QueryPanel from "../components/QueryPanel";
import ResultsPanel from "../components/ResultsPanel";
import SchemaView from "../components/SchemaView";
import StatusBar from "../components/StatusBar";
import StructureDialog from "../components/StructureDialog";
import TopBar from "../components/TopBar";
import UploadDialog from "../components/UploadDialog";
import type { UploadValues } from "../components/UploadDialog";
import { useSplitter } from "../hooks/useSplitter";
import type { Workspace } from "../hooks/useWorkspace";
import { createFromFileSql } from "../lib/csvFiles";
import { SNIPPET_GROUPS } from "../snippets";
import type { QueryFailure, QueryResponse, TableInfo } from "../types";

type View = "consulta" | "esquema";

const PREVIEW_LIMIT = 100;
const HISTORY_LIMIT = 25;
const SIDEBAR = { initial: 300, min: 220, max: 520 };
const EDITOR = { initial: 240, min: 120, max: 620 };

interface ConsolePageProps {
  workspace: Workspace;
}

/** La consola SQL de las Partes 1 y 2: archivos, editor, resultados, plan y esquema. */
export default function ConsolePage({ workspace }: ConsolePageProps) {
  const { sessionId, tables, totalRows, exclusively, running } = workspace;
  const [view, setView] = useState<View>("consulta");
  const [sql, setSql] = useState("");
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [failure, setFailure] = useState<QueryFailure | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [focusedTable, setFocusedTable] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [pendingDrop, setPendingDrop] = useState<TableInfo | null>(null);
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  const [inspected, setInspected] = useState<TableInfo | null>(null);

  const sidebar = useSplitter(SIDEBAR.initial, SIDEBAR.min, SIDEBAR.max, "x");
  const editor = useSplitter(EDITOR.initial, EDITOR.min, EDITOR.max, "y");

  const showResponse = useCallback(
    async (response: QueryResponse) => {
      setResult(response);
      await workspace.acknowledge(response);
    },
    [workspace.acknowledge],
  );

  const showFailure = useCallback(
    async (error: unknown) => {
      setResult(null);
      setFailure(await workspace.acknowledgeFailure(error));
    },
    [workspace.acknowledgeFailure],
  );

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
      <TopBar
        inTransaction={workspace.inTransaction}
        onRefresh={() => void workspace.refreshTables()}
        onToggleTheme={workspace.toggleTheme}
        page="consola"
        theme={workspace.theme}
      >
        <nav aria-label="Vistas de la consola" className="viewtabs">
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
      </TopBar>

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
                onBackgroundFailure={workspace.noteBackgroundFailure}
                result={result}
                sessionId={sessionId}
                tables={tables}
                theme={workspace.theme}
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
          onFailure={workspace.noteBackgroundFailure}
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
        connected={workspace.connected}
        elapsedMs={result?.elapsed_ms ?? null}
        rowCount={result ? result.total_rows : null}
        tableCount={tables.length}
        totalRows={totalRows}
      />
    </div>
  );
}
