import { Fragment, type ReactNode } from "react";

import { hasFilters, type ResearchFilters } from "../api/types";
import { formatAuthors, formatCost, formatCount, formatDuration, formatPages } from "../format";
import type { AnswerSource, DoneData } from "../research/answerEvents";
import { answerBlocks, citedNumbers, isValidCitation, segmentAnswer } from "../research/citations";
import { FAILURE_COPY } from "../research/failureCopy";
import type { Turn } from "../research/turns";
import { Icon } from "./Icon";

export interface TurnHandlers {
  readonly onCite: (turn: Turn, source: AnswerSource) => void;
  readonly onRetry: (turn: Turn) => void;
  readonly onInspect: (turn: Turn, tab: "retrieval" | "measures") => void;
}

interface TurnViewProps extends TurnHandlers {
  readonly turn: Turn;
  readonly active: boolean;
  /** El fragmento abierto en el lector, para marcar su cita. */
  readonly openChunk: string | null;
}

export function describeFilters(filters: ResearchFilters): string | null {
  if (!hasFilters(filters)) {
    return null;
  }
  const parts: string[] = [];
  if (filters.categories.length > 0) {
    parts.push(filters.categories.join(", "));
  }
  if (filters.yearFrom !== null || filters.yearTo !== null) {
    const from = filters.yearFrom ?? "…";
    const to = filters.yearTo ?? "…";
    parts.push(from === to ? `${from}` : `${from}–${to}`);
  }
  return parts.join(" · ");
}

function CitationKey({
  n,
  turn,
  openChunk,
  onCite,
}: {
  readonly n: number;
  readonly turn: Turn;
  readonly openChunk: string | null;
  readonly onCite: TurnHandlers["onCite"];
}) {
  const source = turn.sources[n - 1];
  if (!isValidCitation(n, turn.sources.length) || source === undefined) {
    return (
      <span className="key key--invalid" title="No corresponde a ningún fragmento enviado al modelo">
        <span className="visually-hidden">cita inválida </span>
        {n}
      </span>
    );
  }
  const label = `Cita ${n}: ${source.paper.title}, ${formatPages(source.page_start, source.page_end)}`;
  return (
    <button
      type="button"
      className="key"
      aria-label={label}
      aria-pressed={openChunk === source.chunk_id}
      title={label}
      onClick={() => onCite(turn, source)}
    >
      {n}
    </button>
  );
}

function InlineText({ text, ...rest }: { readonly text: string; readonly turn: Turn; readonly openChunk: string | null; readonly onCite: TurnHandlers["onCite"] }) {
  return (
    <>
      {segmentAnswer(text).map((segment, index) =>
        segment.kind === "text" ? (
          <Fragment key={index}>{segment.text}</Fragment>
        ) : (
          <span key={index} className="keys">
            {segment.numbers.map((n, position) => (
              <CitationKey key={`${n}-${position}`} n={n} {...rest} />
            ))}
          </span>
        ),
      )}
    </>
  );
}

function AnswerBody({ turn, openChunk, onCite }: { readonly turn: Turn; readonly openChunk: string | null; readonly onCite: TurnHandlers["onCite"] }) {
  const writing = turn.status === "streaming";
  const blocks = answerBlocks(turn.text);
  return (
    <div className="answer" aria-busy={writing}>
      {blocks.map((block, index) => {
        const last = index === blocks.length - 1;
        const caret = writing && last ? <span className="caret" aria-hidden="true" /> : null;
        if (block.kind === "paragraph") {
          return (
            <p key={index}>
              <InlineText text={block.text} turn={turn} openChunk={openChunk} onCite={onCite} />
              {caret}
            </p>
          );
        }
        const List = block.ordered ? "ol" : "ul";
        return (
          <List key={index}>
            {block.items.map((item, position) => (
              <li key={position}>
                <InlineText text={item} turn={turn} openChunk={openChunk} onCite={onCite} />
              </li>
            ))}
            {caret}
          </List>
        );
      })}
      {writing && blocks.length === 0 && <span className="caret" aria-hidden="true" />}
    </div>
  );
}

function Notice({ tone, title, children }: { readonly tone: "info" | "warn" | "danger"; readonly title: string; readonly children?: ReactNode }) {
  return (
    <div className={`notice notice--${tone}`} role={tone === "danger" ? "alert" : "note"}>
      <p className="notice__title">{title}</p>
      {children}
    </div>
  );
}

function DoneNotices({ done, turn }: { readonly done: DoneData; readonly turn: Turn }) {
  const notices: ReactNode[] = [];
  if (!done.complete && done.stop_reason === "max_tokens") {
    notices.push(
      <Notice key="cut" tone="warn" title="Respuesta incompleta: llegó al tope de longitud">
        <p>El texto se cortó antes de terminar. No lo tomes como una respuesta completa; prueba con una pregunta más acotada.</p>
      </Notice>,
    );
  } else if (!done.complete) {
    notices.push(
      <Notice key="refusal" tone="warn" title="El modelo se negó a responder">
        <p>
          Lo detuvo un clasificador de seguridad{done.refusal_category === null ? "" : ` (${done.refusal_category})`}. El texto parcial no es una
          respuesta.
        </p>
      </Notice>,
    );
  }
  if (done.complete && !done.answered_from_documents && turn.sources.length > 0) {
    notices.push(
      <Notice key="absent" tone="info" title="Los documentos no responden esta pregunta">
        <p>El modelo leyó {formatCount(turn.sources.length)} fragmentos y ninguno sostiene una respuesta, así que no cita ninguno.</p>
      </Notice>,
    );
  }
  if (done.invalid_citations.length > 0) {
    const listed = done.invalid_citations.map((n) => `[${n}]`).join(", ");
    notices.push(
      <Notice key="invalid" tone="warn" title={`${done.invalid_citations.length === 1 ? "Una cita no se puede" : "Algunas citas no se pueden"} verificar`}>
        <p>
          {listed} no {done.invalid_citations.length === 1 ? "corresponde" : "corresponden"} a ninguno de los {formatCount(turn.sources.length)} fragmentos
          enviados al modelo. Suele ser una referencia bibliográfica copiada de un pasaje; aparece tachada en el texto.
        </p>
      </Notice>,
    );
  }
  return <>{notices}</>;
}

function NoSources({ turn }: { readonly turn: Turn }) {
  const filters = describeFilters(turn.filters);
  return (
    <Notice tone="info" title="Ningún fragmento del corpus coincide">
      <p>
        {filters === null
          ? "Ni BM25 ni los embeddings encontraron pasajes para esta pregunta. Prueba con los términos del área, en inglés."
          : `Con los filtros ${filters} no queda ningún pasaje. Quita alguno y vuelve a preguntar.`}
      </p>
    </Notice>
  );
}

function FailureNotice({ turn, onRetry, onInspect }: { readonly turn: Turn } & Pick<TurnHandlers, "onRetry" | "onInspect">) {
  const failure = turn.failure;
  if (failure === null) {
    return null;
  }
  const copy = FAILURE_COPY[failure.kind];
  return (
    <Notice tone="danger" title={copy.title}>
      <p>{copy.body}</p>
      {failure.detail !== null && <p className="notice__detail">{failure.detail}</p>}
      {failure.requestId !== null && <p className="notice__detail">Petición {failure.requestId}</p>}
      <div className="notice__actions">
        {failure.retryable && (
          <button type="button" className="button button--quiet" onClick={() => onRetry(turn)}>
            <Icon name="retry" />
            Reintentar
          </button>
        )}
        {copy.offerRetrieval && (
          <button type="button" className="button button--quiet" onClick={() => onInspect(turn, "retrieval")}>
            <Icon name="layers" />
            Ver los fragmentos
          </button>
        )}
      </div>
    </Notice>
  );
}

function SourceList({ turn, openChunk, onCite }: { readonly turn: Turn; readonly openChunk: string | null; readonly onCite: TurnHandlers["onCite"] }) {
  const cited = citedNumbers(turn.text, turn.sources.length);
  return (
    <details className="sources" open={turn.status !== "streaming" || undefined}>
      <summary>
        Fragmentos enviados al modelo <span className="count">{formatCount(turn.sources.length)}</span>
        {cited.size > 0 && <span className="count count--cited">{formatCount(cited.size)} citados</span>}
      </summary>
      <ol>
        {turn.sources.map((source) => (
          <li key={source.chunk_id} data-cited={cited.has(source.n) || undefined}>
            <button type="button" className="source" aria-pressed={openChunk === source.chunk_id} onClick={() => onCite(turn, source)}>
              <span className="source__n">{source.n}</span>
              <span className="source__title">{source.paper.title}</span>
              <span className="source__meta">
                {[formatAuthors(source.paper.authors), source.paper.year, formatPages(source.page_start, source.page_end)]
                  .filter((part) => part !== null && part !== "")
                  .join(" · ")}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </details>
  );
}

function TurnMeta({ turn, onInspect }: { readonly turn: Turn; readonly onInspect: TurnHandlers["onInspect"] }) {
  const done = turn.done;
  const facts: string[] = [];
  if (turn.model !== null) {
    facts.push(turn.model);
  }
  if (turn.firstTextMs !== null) {
    facts.push(`primer texto ${formatDuration(turn.firstTextMs)}`);
  }
  if (turn.totalMs !== null) {
    facts.push(`total ${formatDuration(turn.totalMs)}`);
  }
  if (done !== null) {
    facts.push(`${formatCount(done.usage.input_tokens)} → ${formatCount(done.usage.output_tokens)} tokens`);
    facts.push(formatCost(done.cost_usd));
  }
  return (
    <footer className="turn__meta">
      <p className="turn__facts">{facts.join(" · ")}</p>
      <div className="turn__tools">
        <button type="button" className="button button--quiet" onClick={() => onInspect(turn, "retrieval")}>
          <Icon name="layers" />
          Recuperación
        </button>
        <button type="button" className="button button--quiet" onClick={() => onInspect(turn, "measures")}>
          <Icon name="gauge" />
          Tiempos y costo
        </button>
      </div>
    </footer>
  );
}

function Retrieving({ filters }: { readonly filters: string | null }) {
  return (
    <div className="retrieving" role="status">
      <p>Buscando fragmentos con BM25 y embeddings{filters === null ? "" : ` en ${filters}`}…</p>
      <span className="skeleton" />
      <span className="skeleton" />
      <span className="skeleton skeleton--short" />
    </div>
  );
}

/** Una pregunta con su respuesta, sus citas, sus fragmentos y lo que costó. */
export function TurnView({ turn, active, openChunk, onCite, onRetry, onInspect }: TurnViewProps) {
  const filters = describeFilters(turn.filters);
  const noSources = turn.status === "answered" && turn.sources.length === 0;
  return (
    <article className="turn" data-active={active || undefined} aria-labelledby={`pregunta-${turn.id}`}>
      <header className="turn__question">
        <h2 id={`pregunta-${turn.id}`}>{turn.question}</h2>
        {filters !== null && (
          <p className="turn__filters">
            <Icon name="filter" size={16} />
            {filters}
          </p>
        )}
      </header>
      {turn.status === "retrieving" && <Retrieving filters={filters} />}
      {turn.fallback !== null && (
        <p className="turn__fallback">
          Respondió {turn.fallback.to_model} en lugar de {turn.fallback.from_model}: el primero no aceptó la pregunta.
        </p>
      )}
      {noSources ? <NoSources turn={turn} /> : turn.text !== "" || turn.status === "streaming" ? <AnswerBody turn={turn} openChunk={openChunk} onCite={onCite} /> : null}
      {turn.done !== null && <DoneNotices done={turn.done} turn={turn} />}
      {turn.status === "stopped" && <p className="turn__stopped">Detuviste la respuesta; el texto de arriba está incompleto.</p>}
      <FailureNotice turn={turn} onRetry={onRetry} onInspect={onInspect} />
      {turn.sources.length > 0 && <SourceList turn={turn} openChunk={openChunk} onCite={onCite} />}
      {turn.status !== "retrieving" && <TurnMeta turn={turn} onInspect={onInspect} />}
    </article>
  );
}
