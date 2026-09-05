import { useState } from "react";

import SqlEditor from "./SqlEditor";
import type { Snippet } from "../snippets";

interface QueryPanelProps {
  sql: string;
  running: boolean;
  snippets: Snippet[];
  history: string[];
  onChange: (sql: string) => void;
  onRun: () => void;
}

export default function QueryPanel({
  sql,
  running,
  snippets,
  history,
  onChange,
  onRun,
}: QueryPanelProps) {
  const [openHistory, setOpenHistory] = useState(false);
  const statementCount = sql.split(";").filter((part) => part.trim().length > 0).length;

  return (
    <section className="panel panel--editor">
      <header className="panel__header">
        <h2 className="panel__title">Consultas</h2>
        <span className="panel__hint">
          {statementCount === 1 ? "1 sentencia" : `${statementCount} sentencias`}
        </span>
        <div className="panel__actions">
          <div className="dropdown">
            <button
              className="action"
              disabled={history.length === 0}
              onClick={() => setOpenHistory(!openHistory)}
              type="button"
            >
              Historial
              <span className="action__key">{history.length}</span>
            </button>
            {openHistory && history.length > 0 && (
              <ul className="dropdown__menu">
                {history.map((item, index) => (
                  <li key={index}>
                    <button
                      onClick={() => {
                        onChange(item);
                        setOpenHistory(false);
                      }}
                      type="button"
                    >
                      {item.replace(/\s+/g, " ").slice(0, 90)}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button
            className="action action--primary"
            disabled={running || sql.trim().length === 0}
            onClick={onRun}
            type="button"
          >
            {running ? "Ejecutando…" : "Ejecutar"}
            <span className="action__key">⌘↵</span>
          </button>
        </div>
      </header>
      <SqlEditor onChange={onChange} onRun={onRun} value={sql} />
      <div className="snippets">
        {snippets.map((snippet) => (
          <button
            className="snippet"
            key={snippet.label}
            onClick={() => onChange(snippet.sql)}
            title={snippet.description}
            type="button"
          >
            {snippet.label}
          </button>
        ))}
      </div>
    </section>
  );
}
