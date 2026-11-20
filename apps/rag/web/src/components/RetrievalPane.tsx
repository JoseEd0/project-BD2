import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";

import type { ExecutedQuery, ResearchFilters, SearchHit, SearchMode } from "../api/types";
import { formatAuthors, formatCount, formatPages, formatScore } from "../format";
import { RETRIEVAL_MODES, RetrievalError, useRetrieval, type RetrievalResult } from "../research/useRetrieval";
import { StageBars } from "./StageBars";

const MODE_LABELS: Readonly<Record<SearchMode, string>> = {
  bm25: "BM25",
  vector: "Embeddings",
  hybrid: "Fusión RRF",
};

const MODE_NOTES: Readonly<Record<SearchMode, string>> = {
  bm25: "Las palabras exactas, sobre el índice invertido del gestor (SPIMI). No entiende otro idioma.",
  vector: "Los vecinos del embedding de la pregunta en el HNSW por coseno. Encuentra paráfrasis y responde en otro idioma.",
  hybrid: "Las dos listas fusionadas por puesto: cada una suma 1/(60 + puesto). Es el orden que lee el modelo.",
};

const STAGE_LABELS: Readonly<Record<string, string>> = {
  filter: "Papers del filtro",
  embedding: "Embedding de la pregunta",
  bm25: "BM25",
  vector: "HNSW",
  papers: "Metadatos de papers",
};

/** Cuántos parámetros de una lista larga (los papers de un filtro) se muestran antes de resumir. */
const SHOWN_PARAMETERS = 4;
const SNIPPET_CHARACTERS = 220;

function snippet(text: string): string {
  return text.length <= SNIPPET_CHARACTERS ? text : `${text.slice(0, SNIPPET_CHARACTERS).trimEnd()}…`;
}

function Parameters({ values }: { readonly values: readonly string[] }) {
  const shown = values.slice(0, SHOWN_PARAMETERS);
  const hidden = values.length - shown.length;
  return (
    <dl className="sql__parameters">
      {shown.map((value, index) => (
        <div key={index}>
          <dt>${index + 1}</dt>
          <dd>{value}</dd>
        </div>
      ))}
      {hidden > 0 && (
        <div>
          <dt>…</dt>
          <dd>
            y {formatCount(hidden)} parámetros más (${SHOWN_PARAMETERS + 1} a ${values.length})
          </dd>
        </div>
      )}
    </dl>
  );
}

function SqlBlock({ query }: { readonly query: ExecutedQuery }) {
  return (
    <figure className="sql">
      <figcaption>{STAGE_LABELS[query.stage] ?? query.stage}</figcaption>
      <pre>
        <code>{query.sql}</code>
      </pre>
      <Parameters values={query.parameters} />
    </figure>
  );
}

function rankCell(score: { rank: number; score: number } | null | undefined) {
  if (score === null || score === undefined) {
    return <td className="num num--absent">—</td>;
  }
  return (
    <td className="num">
      <strong>{score.rank}</strong> <span className="num__score">{formatScore(score.score)}</span>
    </td>
  );
}

interface ResultsTableProps {
  readonly mode: SearchMode;
  readonly hits: readonly SearchHit[];
  readonly sentAs: ReadonlyMap<string, number>;
  readonly onOpen: (hit: SearchHit) => void;
}

/** Las columnas no cambian entre BM25, embeddings y fusión: solo se llenan distinto. */
function ResultsTable({ mode, hits, sentAs, onOpen }: ResultsTableProps) {
  return (
    <div className="table-scroll">
      <table className="results">
        <caption className="visually-hidden">Fragmentos de {MODE_LABELS[mode]}, de mejor a peor</caption>
        <thead>
          <tr>
            <th scope="col" className="num">#</th>
            <th scope="col" className="num">BM25</th>
            <th scope="col" className="num">Coseno</th>
            <th scope="col" className="num">RRF</th>
            <th scope="col">Fragmento</th>
          </tr>
        </thead>
        <tbody>
          {hits.map((hit) => {
            const n = sentAs.get(hit.chunk_id);
            return (
              <tr key={hit.chunk_id} data-sent={n !== undefined || undefined}>
                <td className="num">{hit.rank}</td>
                {rankCell(hit.bm25)}
                {rankCell(hit.vector)}
                <td className="num">{mode === "hybrid" ? formatScore(hit.score) : <span className="num--absent">—</span>}</td>
                <td>
                  <button type="button" className="hit" onClick={() => onOpen(hit)}>
                    <span className="hit__title">
                      {n !== undefined && <span className="key key--static" title={`Enviado al modelo como [${n}]`}>{n}</span>}
                      {hit.paper.title}
                    </span>
                    <span className="hit__meta">
                      {[formatAuthors(hit.paper.authors), hit.paper.year, formatPages(hit.page_start, hit.page_end)].filter((part) => part !== null && part !== "").join(" · ")}
                    </span>
                    <span className="hit__text">{snippet(hit.text)}</span>
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ModeResult({
  mode,
  query,
  sentAs,
  onOpen,
}: {
  readonly mode: SearchMode;
  readonly query: UseQueryResult<RetrievalResult>;
  readonly sentAs: ReadonlyMap<string, number>;
  readonly onOpen: (hit: SearchHit) => void;
}) {
  if (query.isPending) {
    return (
      <div className="retrieving" role="status">
        <p>Buscando con {MODE_LABELS[mode]}…</p>
        <span className="skeleton" />
        <span className="skeleton" />
      </div>
    );
  }
  if (query.isError) {
    const status = query.error instanceof RetrievalError ? query.error.status : null;
    return (
      <div className="notice notice--danger" role="alert">
        <p className="notice__title">La búsqueda falló{status === null ? "" : ` (HTTP ${status})`}</p>
        <button type="button" className="button button--quiet" onClick={() => void query.refetch()}>
          Reintentar
        </button>
      </div>
    );
  }
  const { response, serverTiming } = query.data;
  return (
    <>
      <p className="mode-note">{MODE_NOTES[mode]}</p>
      {response.results.length === 0 ? (
        <p className="empty-result">Ningún fragmento coincide{mode === "bm25" ? ": ninguna palabra de la pregunta está en el índice, o los filtros dejan fuera los que la tienen." : "."}</p>
      ) : (
        <ResultsTable mode={mode} hits={response.results} sentAs={sentAs} onOpen={onOpen} />
      )}
      <h3 className="retrieval__heading">El SQL que corrió en el gestor</h3>
      <div className="sql-list">
        {response.queries.map((executed, index) => (
          <SqlBlock key={index} query={executed} />
        ))}
      </div>
      <StageBars serverTiming={serverTiming} labels={STAGE_LABELS} caption="Tiempo en el servidor" />
    </>
  );
}

interface RetrievalPaneProps {
  /** Lo que buscó el servicio para la respuesta: así cada lista es la que leyó el modelo. */
  readonly query: string;
  readonly filters: ResearchFilters;
  /** Los fragmentos que la respuesta envió al modelo, con su número de cita. */
  readonly sentAs: ReadonlyMap<string, number>;
  readonly onOpen: (hit: SearchHit) => void;
}

/** Lo que trajo cada método para la consulta, el SQL que corrió y cuánto tardó. */
export function RetrievalPane({ query, filters, sentAs, onOpen }: RetrievalPaneProps) {
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const queries = useRetrieval(query, filters);
  const selected = queries[RETRIEVAL_MODES.indexOf(mode)];
  return (
    <section className="retrieval" aria-label="Recuperación">
      <p className="retrieval__query">
        Búsqueda: <q>{query}</q>
      </p>
      <fieldset className="segmented">
        <legend className="visually-hidden">Método de búsqueda</legend>
        {RETRIEVAL_MODES.map((option, index) => (
          <label key={option}>
            <input type="radio" name="retrieval-mode" value={option} checked={mode === option} onChange={() => setMode(option)} />
            <span>
              {MODE_LABELS[option]}
              {queries[index]?.data !== undefined && <span className="segmented__count">{formatCount(queries[index].data.response.results.length)}</span>}
            </span>
          </label>
        ))}
      </fieldset>
      {selected !== undefined && <ModeResult mode={mode} query={selected} sentAs={sentAs} onOpen={onOpen} />}
    </section>
  );
}
