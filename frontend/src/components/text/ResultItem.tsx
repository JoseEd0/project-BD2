import { useMemo } from "react";

import { excerpt, withoutLeadingTitle } from "../../lib/textHighlight";
import type { ResultCard } from "../../lib/textSearch";
import HighlightedText from "./HighlightedText";

/** Caracteres del fragmento del texto que acompaña a cada resultado, y cuántos de ellos van
 * antes de la primera coincidencia. */
const EXCERPT_LENGTH = 260;
const EXCERPT_LEADING = 70;
const SCORE_DIGITS = 4;

interface ResultItemProps {
  rank: number;
  card: ResultCard;
  terms: string[];
  /** Puesto de este mismo documento en el otro ranking; `undefined` si allí no salió. */
  otherRank: number | undefined;
  otherLabel: string;
}

function comparison(rank: number, otherRank: number | undefined, otherLabel: string): string {
  if (otherRank === undefined) {
    return `no sale en ${otherLabel}`;
  }
  return otherRank === rank ? `mismo puesto en ${otherLabel}` : `${otherRank}.º en ${otherLabel}`;
}

/** Un documento del ranking: su puesto, su puntaje, el título y un fragmento resaltados. */
export default function ResultItem({ rank, card, terms, otherRank, otherLabel }: ResultItemProps) {
  const fragment = useMemo(
    () =>
      excerpt(withoutLeadingTitle(card.text, card.title), terms, EXCERPT_LENGTH, EXCERPT_LEADING),
    [card.text, card.title, terms],
  );
  const moved = otherRank === undefined || otherRank !== rank;
  return (
    <li className="result">
      <span className="result__rank">{rank}</span>
      <div className="result__body">
        <h3 className="result__title">
          <HighlightedText terms={terms} text={card.title ?? card.key} />
        </h3>
        <div className="result__meta">
          {card.brand !== null && <span className="chip">{card.brand}</span>}
          <span className="result__score">
            {card.score === null ? "sin puntaje" : card.score.toFixed(SCORE_DIGITS)}
          </span>
          <span className={moved ? "result__other result__other--moved" : "result__other"}>
            {comparison(rank, otherRank, otherLabel)}
          </span>
          <span className="result__key">{card.key}</span>
        </div>
        {fragment.length > 0 && (
          <p className="result__excerpt">
            <HighlightedText terms={terms} text={fragment} />
          </p>
        )}
      </div>
    </li>
  );
}
