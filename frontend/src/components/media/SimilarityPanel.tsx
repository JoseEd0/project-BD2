import CopyButton from "../CopyButton";
import FailureNotice from "../FailureNotice";
import SqlBlock from "../SqlBlock";
import {
  EFFORT_OPTION,
  METRIC_LABEL,
  VECTOR_METRICS,
  formatSimilarity,
  mediaKindOf,
  sameCategoryCount,
  similarityShare,
} from "../../lib/mediaSearch";
import type {
  MediaCard,
  PanelOutcome,
  PanelSettings,
  VectorMethod,
  VectorMetric,
} from "../../lib/mediaSearch";
import MediaThumb from "./MediaThumb";

interface SimilarityPanelProps {
  label: string;
  methods: readonly VectorMethod[];
  settings: PanelSettings;
  onSettings: (patch: Partial<PanelSettings>) => void;
  /** El SQL de lo elegido ahora; el resultado puede ser de uno anterior mientras llega. */
  sql: string;
  outcome: PanelOutcome | null;
  queryCategory: string | null;
  stale: boolean;
  failureShownAbove: boolean;
  onOpenInConsole: (sql: string) => void;
}

/** Las cláusulas que cambian entre las dos consultas, escritas como en el SQL. */
function Clauses({
  methods,
  settings,
  onSettings,
}: Pick<SimilarityPanelProps, "methods" | "settings" | "onSettings">) {
  const effortName = EFFORT_OPTION[settings.method];
  return (
    <div className="clauses__list mediapanel__clauses">
      <label className="clause">
        <span className="clause__keyword">USING</span>
        <select
          aria-label="Índice"
          className="clause__input"
          disabled={methods.length < 2}
          onChange={(event) => onSettings({ method: event.target.value as VectorMethod, effort: "" })}
          value={settings.method}
        >
          {methods.map((method) => (
            <option key={method}>{method}</option>
          ))}
        </select>
      </label>
      <label className="clause">
        <span className="clause__keyword">METRIC</span>
        <span className="clause__operator">=</span>
        <select
          aria-label="Métrica"
          className="clause__input"
          onChange={(event) => onSettings({ metric: event.target.value as VectorMetric })}
          value={settings.metric}
        >
          {VECTOR_METRICS.map((metric) => (
            <option key={metric} value={metric}>
              {metric}
            </option>
          ))}
        </select>
      </label>
      <label className="clause">
        <span className="clause__parameter">{effortName}</span>
        <span className="clause__operator">=</span>
        <input
          aria-label={effortName}
          className="clause__input clause__input--number mediapanel__effort"
          inputMode="numeric"
          onChange={(event) => onSettings({ effort: event.target.value })}
          placeholder="del índice"
          value={settings.effort}
        />
      </label>
    </div>
  );
}

function Summary({ outcome, category }: { outcome: PanelOutcome | null; category: string | null }) {
  if (outcome === null || outcome.failure !== null) {
    return null;
  }
  const rows = outcome.cards.length;
  const same = sameCategoryCount(outcome.cards, category);
  return (
    <span className="rankpanel__summary">
      {rows} {rows === 1 ? "fila" : "filas"}
      {outcome.elapsedMs !== null && <> en {outcome.elapsedMs.toFixed(2)} ms</>}
      {same !== null && (
        <>
          {" "}
          · {same} de {rows} de su clase
        </>
      )}
    </span>
  );
}

function Gallery({ cards, category }: { cards: MediaCard[]; category: string | null }) {
  const best = cards[0]?.score ?? null;
  return (
    <table className="hits mediagallery">
      <thead>
        <tr>
          <th className="hits__rank" scope="col">
            #
          </th>
          <th className="mediagallery__media" scope="col">
            Archivo
          </th>
          <th scope="col">Fila</th>
          <th className="hits__score" scope="col">
            Similitud
          </th>
        </tr>
      </thead>
      <tbody>
        {cards.map((card, index) => {
          const kind = mediaKindOf(card.path);
          const same = category !== null && card.category === category;
          return (
            <tr key={card.key}>
              <td className="hits__rank">{index + 1}</td>
              <td className="mediagallery__media">
                {kind !== null && <MediaThumb kind={kind} label={card.title ?? card.path} path={card.path} />}
              </td>
              <td className="hits__document">
                <div className="hits__title">{card.title ?? card.path}</div>
                <div className="hits__meta">
                  {card.category !== null && (
                    <span className={same ? "badge badge--match" : "badge"}>
                      {same && <span className="visually-hidden">Misma clase que la consulta: </span>}
                      {card.category}
                    </span>
                  )}
                  <code className="hits__key">{card.key}</code>
                </div>
              </td>
              <td className="hits__score">
                <span className="hits__value">{card.score === null ? "—" : formatSimilarity(card.score)}</span>
                <span aria-hidden="true" className="hits__bar">
                  <span className="hits__fill" style={{ width: `${similarityShare(card.score, best) * 100}%` }} />
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Una de las dos consultas: sus cláusulas, el SQL exacto y la galería que devolvió. */
export default function SimilarityPanel({
  label,
  methods,
  settings,
  onSettings,
  sql,
  outcome,
  queryCategory,
  stale,
  failureShownAbove,
  onOpenInConsole,
}: SimilarityPanelProps) {
  const title = `${label} · ${settings.method}, ${METRIC_LABEL[settings.metric]}`;
  return (
    <section aria-label={title} className="rankpanel mediapanel">
      <header className="rankpanel__header">
        <h2 className="rankpanel__title">{title}</h2>
        <Summary category={queryCategory} outcome={outcome} />
        <span className="rankpanel__spacer" />
        <CopyButton text={sql} />
        <button className="action action--quiet" onClick={() => onOpenInConsole(sql)} type="button">
          Abrir en la consola
        </button>
      </header>
      <Clauses methods={methods} onSettings={onSettings} settings={settings} />
      <SqlBlock sql={sql} />
      <div className={stale ? "rankpanel__body rankpanel__body--stale" : "rankpanel__body"}>
        {outcome !== null && outcome.failure !== null && !failureShownAbove && (
          <div className="rankpanel__notice">
            <FailureNotice failure={outcome.failure} />
          </div>
        )}
        {outcome !== null && outcome.failure === null && outcome.cards.length === 0 && (
          <p className="rankpanel__empty">La tabla no tiene filas con archivo para comparar.</p>
        )}
        {outcome !== null && outcome.failure === null && outcome.cards.length > 0 && (
          <Gallery cards={outcome.cards} category={queryCategory} />
        )}
      </div>
    </section>
  );
}
