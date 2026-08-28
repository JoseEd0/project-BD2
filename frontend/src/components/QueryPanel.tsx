import type { KeyboardEvent } from "react";

interface QueryPanelProps {
  sql: string;
  running: boolean;
  snippets: { label: string; sql: string }[];
  onChange: (sql: string) => void;
  onRun: () => void;
  onPickSnippet: (sql: string) => void;
}

export default function QueryPanel({
  sql,
  running,
  snippets,
  onChange,
  onRun,
  onPickSnippet,
}: QueryPanelProps) {
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      onRun();
    }
  }

  return (
    <section className="panel panel--editor">
      <header className="panel__header">
        <h2 className="panel__title">Consultas</h2>
        <span className="panel__hint">SQL</span>
        <button
          className="action action--primary"
          disabled={running || sql.trim().length === 0}
          onClick={onRun}
          style={{ marginLeft: "auto" }}
          type="button"
        >
          {running ? "Ejecutando…" : "Ejecutar"}
          <span className="action__key">⌘↵</span>
        </button>
      </header>
      <div className="editor">
        <textarea
          className="editor__input"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="SELECT * FROM alumnos WHERE id = 1;"
          spellCheck={false}
          value={sql}
        />
        <div className="snippets">
          {snippets.map((snippet) => (
            <button
              className="snippet"
              key={snippet.label}
              onClick={() => onPickSnippet(snippet.sql)}
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
