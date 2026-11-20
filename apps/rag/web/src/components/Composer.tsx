import { useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { hasFilters, type ResearchFilters } from "../api/types";
import { FilterBar } from "./FilterBar";
import { Icon } from "./Icon";

/** El tope del servicio (`MAX_QUERY_CHARACTERS` en `rag.api`). */
export const MAX_QUESTION_CHARACTERS = 1000;
/** Desde cuántos caracteres se muestra cuánto queda. */
const COUNTER_FROM = 800;

interface ComposerProps {
  readonly busy: boolean;
  readonly filters: ResearchFilters;
  readonly onFiltersChange: (filters: ResearchFilters) => void;
  readonly onAsk: (question: string) => void;
  readonly onStop: () => void;
}

/**
 * La pregunta. Enter pregunta; Mayúsculas+Enter, una línea nueva. Los filtros por año y
 * categoría quedan plegados en «Opciones»: casi nadie los necesita, y el año o el tema se
 * pueden decir en la pregunta.
 */
export function Composer({ busy, filters, onFiltersChange, onAsk, onStop }: ComposerProps) {
  const [question, setQuestion] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  const hintId = useId();
  const trimmed = question.trim();

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (busy || trimmed === "") {
      return;
    }
    onAsk(trimmed);
    setQuestion("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      submit(event);
    }
  }

  return (
    <form className="composer" onSubmit={submit} aria-label="Nueva pregunta">
      <div className="composer__field">
        <label htmlFor="pregunta" className="visually-hidden">
          Pregunta
        </label>
        <textarea
          id="pregunta"
          ref={field}
          rows={2}
          value={question}
          maxLength={MAX_QUESTION_CHARACTERS}
          placeholder="Pregunta o repregunta sobre los papers…"
          aria-describedby={hintId}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
        />
        {busy ? (
          <button type="button" className="button button--primary composer__send" aria-label="Detener" title="Detener" onClick={onStop}>
            <Icon name="stop" />
          </button>
        ) : (
          <button type="submit" className="button button--primary composer__send" aria-label="Preguntar" title="Preguntar" disabled={trimmed === ""}>
            <Icon name="send" />
          </button>
        )}
      </div>
      <div className="composer__foot">
        <details className="composer__options" open={hasFilters(filters) || undefined}>
          <summary>{hasFilters(filters) ? "Opciones (filtros activos)" : "Opciones"}</summary>
          <FilterBar filters={filters} onChange={onFiltersChange} />
        </details>
        <p id={hintId} className="composer__hint">
          {question.length >= COUNTER_FROM
            ? `${MAX_QUESTION_CHARACTERS - question.length} caracteres disponibles`
            : "Enter para preguntar, Mayús + Enter para otra línea"}
        </p>
      </div>
    </form>
  );
}
