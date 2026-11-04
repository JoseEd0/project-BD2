import FailureNotice from "../FailureNotice";
import { RANKING_LABEL } from "../../lib/textSearch";
import type { RankingMethod, RankingOutcome } from "../../lib/textSearch";
import type { TextSearchInfo } from "../../types";
import ResultItem from "./ResultItem";

interface RankingColumnProps {
  outcome: RankingOutcome;
  terms: string[];
  otherPositions: Map<string, number>;
  otherMethod: RankingMethod;
}

const PARAMETER_DIGITS = 2;

function parameters(text: TextSearchInfo | null): string | null {
  if (text === null || text.k1 === null || text.b === null) {
    return null;
  }
  const format = (value: number) =>
    value.toLocaleString("es", { maximumFractionDigits: PARAMETER_DIGITS });
  return `k1 = ${format(text.k1)} · b = ${format(text.b)}`;
}

/** Una columna de resultados: el ranking de un método, comparado puesto a puesto con el
 * otro. */
export default function RankingColumn({
  outcome,
  terms,
  otherPositions,
  otherMethod,
}: RankingColumnProps) {
  const used = parameters(outcome.text);
  return (
    <section aria-label={`Ranking ${RANKING_LABEL[outcome.method]}`} className="ranking">
      <header className="ranking__header">
        <h2 className="ranking__title">{RANKING_LABEL[outcome.method]}</h2>
        {used !== null && <span className="ranking__params">{used}</span>}
        {outcome.failure === null && (
          <span className="panel__hint">
            {outcome.cards.length} resultado(s)
            {outcome.elapsedMs !== null && ` · ${outcome.elapsedMs.toFixed(2)} ms`}
          </span>
        )}
      </header>
      {outcome.failure !== null && <FailureNotice failure={outcome.failure} />}
      {outcome.failure === null && outcome.cards.length === 0 && (
        <p className="ranking__empty">Ningún documento tiene los términos de la consulta.</p>
      )}
      {outcome.cards.length > 0 && (
        <ol className="ranking__list">
          {outcome.cards.map((card, index) => (
            <ResultItem
              card={card}
              key={card.key}
              otherLabel={RANKING_LABEL[otherMethod]}
              otherRank={otherPositions.get(card.key)}
              rank={index + 1}
              terms={terms}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
