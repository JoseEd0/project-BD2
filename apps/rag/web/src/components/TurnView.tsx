import { Fragment, type ReactNode } from "react";

import { hasFilters, type ResearchFilters } from "../api/types";
import { formatAuthors, formatCost, formatCount, formatDuration, formatPages } from "../format";
import type { AnswerSource, DoneData } from "../research/answerEvents";
import { citedNumbers, isValidCitation } from "../research/citations";
import { answerBlocks, inlineNodes, type AnswerBlock, type InlineNode } from "../research/markdown";
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

interface CitationContext {
  readonly turn: Turn;
  readonly openChunk: string | null;
  readonly onCite: TurnHandlers["onCite"];
}

function Inline({ nodes, context }: { readonly nodes: readonly InlineNode[]; readonly context: CitationContext }) {
  return (
    <>
      {nodes.map((node, index) => {
        switch (node.kind) {
          case "text":
            return <Fragment key={index}>{node.text}</Fragment>;
          case "code":
            return <code key={index}>{node.text}</code>;
          case "strong":
            return (
              <strong key={index}>
                <Inline nodes={node.children} context={context} />
              </strong>
            );
          case "em":
            return (
              <em key={index}>
                <Inline nodes={node.children} context={context} />
              </em>
            );
          case "citation":
            return (
              <span key={index} className="keys">
                {node.numbers.map((n, position) => (
                  <CitationKey key={`${n}-${position}`} n={n} {...context} />
                ))}
              </span>
            );
        }
      })}
    </>
  );
}

function InlineText({ text, context }: { readonly text: string; readonly context: CitationContext }) {
  return <Inline nodes={inlineNodes(text)} context={context} />;
}

function Block({ block, context, caret }: { readonly block: AnswerBlock; readonly context: CitationContext; readonly caret: ReactNode }) {
  switch (block.kind) {
    case "paragraph":
      return (
        <p>
          <InlineText text={block.text} context={context} />
          {caret}
        </p>
      );
    case "heading":
      return (
        <p className="answer__heading">
          <InlineText text={block.text} context={context} />
          {caret}
        </p>
      );
    case "quote":
      return (
        <blockquote>
          <InlineText text={block.text} context={context} />
          {caret}
        </blockquote>
      );
    case "code":
      return (
        <pre>
          <code>{block.text}</code>
          {caret}
        </pre>
      );
    case "list": {
      const List = block.ordered ? "ol" : "ul";
      return (
        <List>
          {block.items.map((item, position) => (
            <li key={position}>
              <InlineText text={item} context={context} />
              {position === block.items.length - 1 && caret}
            </li>
          ))}
        </List>
      );
    }
  }
}

function AnswerBody({ turn, openChunk, onCite }: CitationContext) {
  const writing = turn.status === "streaming";
  const blocks = answerBlocks(turn.text);
  const context: CitationContext = { turn, openChunk, onCite };
  return (
    <div className="answer" aria-busy={writing}>
      {blocks.map((block, index) => (
        <Block
          key={index}
          block={block}
          context={context}
          caret={writing && index === blocks.length - 1 ? <span className="caret" aria-hidden="true" /> : null}
        />
      ))}
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
  if (done.complete && !done.answered_from_documents && done.conversational !== true && turn.sources.length > 0) {
    notices.push(
      <p key="absent" className="turn__note">
        Sin citas: ninguno de los {formatCount(turn.sources.length)} fragmentos que leyó el modelo sostiene una respuesta.
      </p>,
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
          ? "Ni BM25 ni los embeddings encontraron pasajes para esta pregunta. Prueba con los términos técnicos del área, mejor en inglés: «learned index», «BM25», «HNSW»."
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
  const noSources = turn.status === "answered" && turn.sources.length === 0 && turn.done?.conversational !== true;
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
