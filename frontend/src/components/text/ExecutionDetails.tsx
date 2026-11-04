import { useState } from "react";

import PlanPanel from "../PlanPanel";
import SqlBlock from "../SqlBlock";
import { RANKING_LABEL } from "../../lib/textSearch";
import type { RankingMethod, RankingOutcome } from "../../lib/textSearch";

interface ExecutionDetailsProps {
  outcomes: RankingOutcome[];
}

/** El SQL que la página mandó por `POST /query` y el plan con que lo respondió el motor,
 * uno de los dos rankings a la vez. */
export default function ExecutionDetails({ outcomes }: ExecutionDetailsProps) {
  const [chosen, setChosen] = useState<RankingMethod>("BM25");
  const shown = outcomes.find((outcome) => outcome.method === chosen) ?? outcomes[0];
  return (
    <section className="execution">
      <header className="execution__header">
        <h2 className="section-title">Cómo se ejecutó</h2>
        <div className="segmented" role="tablist">
          {outcomes.map((outcome) => (
            <button
              aria-selected={outcome.method === shown.method}
              className={
                outcome.method === shown.method
                  ? "segmented__item segmented__item--active"
                  : "segmented__item"
              }
              key={outcome.method}
              onClick={() => setChosen(outcome.method)}
              role="tab"
              type="button"
            >
              {RANKING_LABEL[outcome.method]}
            </button>
          ))}
        </div>
      </header>
      <div className="execution__body">
        <div className="execution__sql">
          <h3 className="execution__caption">SQL enviado a POST /query</h3>
          <SqlBlock sql={shown.sql} />
        </div>
        <PlanPanel plan={shown.plan} />
      </div>
    </section>
  );
}
