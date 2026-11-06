import { useMemo } from "react";

import CopyButton from "../CopyButton";
import FailureNotice from "../FailureNotice";
import SqlBlock from "../SqlBlock";
import { excerpt, withoutLeadingTitle } from "../../lib/textHighlight";
import { RANKING_LABEL, RANKING_SHORT_LABEL, formatScore, scoreShare } from "../../lib/textSearch";
import type { RankingMethod, RankingOutcome, ResultCard } from "../../lib/textSearch";
import HighlightedText from "./HighlightedText";

/** Caracteres del fragmento del texto que acompaña a cada fila, y cuántos de ellos van
 * antes de la primera coincidencia. */
const EXCERPT_LENGTH = 220;
const EXCERPT_LEADING = 60;

interface RankingPanelProps {
  method: RankingMethod;
  /** El SQL de lo que está escrito ahora; el resultado puede ser de un SQL anterior mientras
   * llega el nuevo. */
  sql: string;
  outcome: RankingOutcome | null;
  terms: string[];
  /** Puesto de cada documento en el otro ranking, por su clave. */
  otherPositions: Map<string, number>;
  otherMethod: RankingMethod;
  stale: boolean;
  /** El error del motor ya se ve una vez para los dos rankings. */
  failureShownAbove: boolean;
  onOpenInConsole: (sql: string) => void;
}

function Summary({ outcome }: { outcome: RankingOutcome | null }) {
  if (outcome === null || outcome.failure !== null) {
    return null;
  }
  const rows = outcome.cards.length;
  return (
    <span className="rankpanel__summary">
      {rows} {rows === 1 ? "fila" : "filas"}
      {outcome.elapsedMs !== null && <> en {outcome.elapsedMs.toFixed(2)} ms</>}
    </span>
  );
}

function ordinal(position: number | undefined): string {
  return position === undefined ? "—" : `${position}.º`;
}

function HitRow({
  rank,
  card,
  method,
  best,
  terms,
  otherRank,
  otherLabel,
}: {
  rank: number;
  card: ResultCard;
  method: RankingMethod;
  best: number | null;
  terms: string[];
  otherRank: number | undefined;
  otherLabel: string;
}) {
  const fragment = useMemo(
    () =>
      excerpt(withoutLeadingTitle(card.text, card.title), terms, EXCERPT_LENGTH, EXCERPT_LEADING),
    [card.text, card.title, terms],
  );
  const share = scoreShare(card.score, best);
  const otherTitle =
    otherRank === undefined
      ? `No está en el resultado de ${otherLabel}`
      : `Con ${otherLabel} sale en el puesto ${otherRank}`;
  return (
    <tr>
      <td className="hits__rank">{rank}</td>
      <td className="hits__document">
        <div className="hits__title">
          <HighlightedText terms={terms} text={card.title ?? card.key} />
        </div>
        <div className="hits__meta">
          {card.brand !== null && <span className="hits__brand">{card.brand}</span>}
          <code className="hits__key">{card.key}</code>
        </div>
        {fragment.length > 0 && fragment.trim() !== card.brand && (
          <p className="hits__excerpt">
            <HighlightedText terms={terms} text={fragment} />
          </p>
        )}
      </td>
      <td className="hits__score">
        <span className="hits__value">{card.score === null ? "—" : formatScore(card.score, method)}</span>
        <span aria-hidden="true" className="hits__bar">
          <span className="hits__fill" style={{ width: `${share * 100}%` }} />
        </span>
      </td>
      <td
        className={otherRank === rank ? "hits__other hits__other--same" : "hits__other"}
        title={otherTitle}
      >
        {ordinal(otherRank)}
      </td>
    </tr>
  );
}

function Hits({
  outcome,
  terms,
  otherPositions,
  otherMethod,
  failureShownAbove,
}: {
  outcome: RankingOutcome;
  terms: string[];
  otherPositions: Map<string, number>;
  otherMethod: RankingMethod;
  failureShownAbove: boolean;
}) {
  if (outcome.failure !== null && failureShownAbove) {
    return null;
  }
  if (outcome.failure !== null) {
    return (
      <div className="rankpanel__notice">
        <FailureNotice failure={outcome.failure} />
      </div>
    );
  }
  if (outcome.cards.length === 0) {
    return (
      <p className="rankpanel__empty">
        Ningún documento cumple la consulta. Prueba con otras palabras o quita una condición.
      </p>
    );
  }
  const best = outcome.cards[0].score;
  const otherLabel = RANKING_SHORT_LABEL[otherMethod];
  return (
    <table className="hits">
      <thead>
        <tr>
          <th className="hits__rank" scope="col">
            #
          </th>
          <th scope="col">Documento</th>
          <th className="hits__score" scope="col">
            Puntaje
          </th>
          <th className="hits__other" scope="col" title={`Puesto del mismo documento con ${otherLabel}`}>
            Con {otherLabel}
          </th>
        </tr>
      </thead>
      <tbody>
        {outcome.cards.map((card, index) => (
          <HitRow
            best={best}
            card={card}
            key={card.key}
            method={outcome.method}
            otherLabel={otherLabel}
            otherRank={otherPositions.get(card.key)}
            rank={index + 1}
            terms={terms}
          />
        ))}
      </tbody>
    </table>
  );
}

/** Uno de los dos rankings: la consulta SQL que lo pide y la tabla que devolvió. */
export default function RankingPanel({
  method,
  sql,
  outcome,
  terms,
  otherPositions,
  otherMethod,
  stale,
  failureShownAbove,
  onOpenInConsole,
}: RankingPanelProps) {
  return (
    <section aria-label={`Ranking con ${RANKING_LABEL[method]}`} className="rankpanel">
      <header className="rankpanel__header">
        <h2 className="rankpanel__title">{RANKING_LABEL[method]}</h2>
        <Summary outcome={outcome} />
        <span className="rankpanel__spacer" />
        <CopyButton text={sql} />
        <button className="action action--quiet" onClick={() => onOpenInConsole(sql)} type="button">
          Abrir en la consola
        </button>
      </header>
      <SqlBlock sql={sql} />
      <div className={stale ? "rankpanel__body rankpanel__body--stale" : "rankpanel__body"}>
        {outcome !== null && (
          <Hits
            failureShownAbove={failureShownAbove}
            otherMethod={otherMethod}
            otherPositions={otherPositions}
            outcome={outcome}
            terms={terms}
          />
        )}
      </div>
    </section>
  );
}
