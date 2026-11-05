import { useState } from "react";

import InvertedIndexView from "../InvertedIndexView";
import { PlanView } from "../PlanPanel";
import { RANKING_LABEL } from "../../lib/textSearch";
import type { RankingMethod, RankingOutcome } from "../../lib/textSearch";
import type { InvertedStructure } from "../../types";

type Tab = RankingMethod | "index";

interface DetailTabsProps {
  outcomes: RankingOutcome[];
  indexName: string | null;
  index: InvertedStructure | null;
  indexError: string | null;
}

function tabLabel(tab: Tab, indexName: string | null): string {
  if (tab === "index") {
    return indexName === null ? "Índice invertido" : `Índice ${indexName}`;
  }
  return `Plan con ${RANKING_LABEL[tab]}`;
}

/** Lo que hay detrás de los dos rankings: el plan con que el motor respondió cada consulta y
 * el índice invertido que leyeron. */
export default function DetailTabs({ outcomes, indexName, index, indexError }: DetailTabsProps) {
  const [chosen, setChosen] = useState<Tab>("BM25");
  const tabs: Tab[] = [...outcomes.map((outcome) => outcome.method), "index"];
  const shown = tabs.includes(chosen) ? chosen : tabs[0];
  const plan = outcomes.find((outcome) => outcome.method === shown)?.plan ?? null;
  return (
    <section className="details">
      <div aria-label="Detalle de la ejecución" className="details__tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            aria-selected={tab === shown}
            className={tab === shown ? "viewtab viewtab--active" : "viewtab"}
            key={tab}
            onClick={() => setChosen(tab)}
            role="tab"
            type="button"
          >
            {tabLabel(tab, indexName)}
          </button>
        ))}
      </div>
      <div className="details__body" role="tabpanel">
        {shown !== "index" && <PlanView plan={plan} />}
        {shown === "index" && index !== null && <InvertedIndexView inverted={index} />}
        {shown === "index" && index === null && (
          <p className="panel__empty">{indexError ?? "Leyendo el índice…"}</p>
        )}
      </div>
    </section>
  );
}
