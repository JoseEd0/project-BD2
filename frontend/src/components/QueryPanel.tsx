import { useState } from "react";

import SqlEditor from "./SqlEditor";
import { countStatements } from "../lib/highlight";
import type { SnippetGroup } from "../snippets";

interface QueryPanelProps {
  sql: string;
  running: boolean;
  snippetGroups: SnippetGroup[];
  history: string[];
  onChange: (sql: string) => void;
  onRun: () => void;
}

/** Caracteres de cada consulta que se ven en el desplegable del historial. */
const HISTORY_PREVIEW_LENGTH = 90;

export default function QueryPanel({
  sql,
  running,
  snippetGroups,
  history,
  onChange,
  onRun,
}: QueryPanelProps) {
  const [openHistory, setOpenHistory] = useState(false);
  const [activeGroup, setActiveGroup] = useState(snippetGroups[0]?.name ?? "");
  const statementCount = countStatements(sql);
  const visibleSnippets =
    snippetGroups.find((group) => group.name === activeGroup)?.snippets ?? [];

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
                      {item.replace(/\s+/g, " ").slice(0, HISTORY_PREVIEW_LENGTH)}
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
        <div className="snippets__groups" role="tablist">
          {snippetGroups.map((group) => (
            <button
              aria-selected={group.name === activeGroup}
              className={
                group.name === activeGroup
                  ? "snippets__group snippets__group--active"
                  : "snippets__group"
              }
              key={group.name}
              onClick={() => setActiveGroup(group.name)}
              role="tab"
              type="button"
            >
              {group.name}
            </button>
          ))}
        </div>
        <div className="snippets__items">
          {visibleSnippets.map((snippet) => (
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
      </div>
    </section>
  );
}
