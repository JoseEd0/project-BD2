import { useState } from "react";

import { PlanView } from "../PlanPanel";
import SimilarityIndexView from "../SimilarityIndexView";
import type { PanelOutcome } from "../../lib/mediaSearch";
import type { SimilarityStructure } from "../../types";

interface IndexTab {
  name: string;
  structure: SimilarityStructure;
}

interface MediaDetailsProps {
  labels: readonly string[];
  outcomes: readonly (PanelOutcome | null)[];
  indexes: readonly IndexTab[];
  indexError: string | null;
}

/** Lo que hay detrás de las dos consultas: el plan de cada una y los índices de la columna. */
export default function MediaDetails({ labels, outcomes, indexes, indexError }: MediaDetailsProps) {
  const tabs = [
    ...labels.map((label, position) => ({ id: `plan-${position}`, title: `Plan de ${label}` })),
    ...indexes.map((index) => ({ id: `index-${index.name}`, title: `Índice ${index.name}` })),
  ];
  const [chosen, setChosen] = useState(tabs[0]?.id ?? "");
  const shown = tabs.some((tab) => tab.id === chosen) ? chosen : tabs[0]?.id;
  const planAt = labels.findIndex((_, position) => shown === `plan-${position}`);
  const index = indexes.find((item) => shown === `index-${item.name}`);
  return (
    <section className="details">
      <div aria-label="Detalle de la ejecución" className="details__tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            aria-selected={tab.id === shown}
            className={tab.id === shown ? "viewtab viewtab--active" : "viewtab"}
            key={tab.id}
            onClick={() => setChosen(tab.id)}
            role="tab"
            type="button"
          >
            {tab.title}
          </button>
        ))}
      </div>
      <div className="details__body" role="tabpanel">
        {planAt >= 0 && <PlanView plan={outcomes[planAt]?.plan ?? null} />}
        {index !== undefined && <SimilarityIndexView index={index.structure} />}
        {planAt < 0 && index === undefined && (
          <p className="panel__empty">{indexError ?? "Leyendo el índice…"}</p>
        )}
      </div>
    </section>
  );
}
