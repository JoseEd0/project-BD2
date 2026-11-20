import { lazy, Suspense, useId, useMemo, type KeyboardEvent } from "react";

import type { SearchHit } from "../api/types";
import { citedPages, type ReaderTarget } from "../research/readerTarget";
import type { Turn } from "../research/turns";
import { Icon, type IconName } from "./Icon";
import { MeasuresPane } from "./MeasuresPane";
import { RetrievalPane } from "./RetrievalPane";

/** pdf.js pesa más que el resto de la app junta: llega cuando se abre la primera cita. */
const ReaderPane = lazy(() => import("../reader/ReaderPane"));

export type WorkbenchTab = "page" | "retrieval" | "measures";

const TABS: readonly { id: WorkbenchTab; label: string; icon: IconName }[] = [
  { id: "page", label: "Página", icon: "page" },
  { id: "retrieval", label: "Recuperación", icon: "layers" },
  { id: "measures", label: "Tiempos y costo", icon: "gauge" },
];

interface WorkbenchProps {
  readonly turn: Turn | null;
  readonly target: ReaderTarget | null;
  readonly tab: WorkbenchTab;
  readonly onTab: (tab: WorkbenchTab) => void;
  readonly onPage: (page: number) => void;
  readonly onOpenHit: (hit: SearchHit) => void;
}

function EmptyPage({ hasTurn }: { readonly hasTurn: boolean }) {
  return (
    <div className="bench-empty">
      <p className="bench-empty__title">{hasTurn ? "Elige una cita para ver su página" : "Aquí se abre la página de cada cita"}</p>
      <p>
        {hasTurn
          ? "Cada número entre corchetes de la respuesta, y cada fragmento de la lista, abre el PDF en la página de donde sale, con el pasaje marcado."
          : "Haz una pregunta. Las citas de la respuesta abren el paper aquí, en la página exacta, y la recuperación muestra qué trajo cada método y con qué SQL."}
      </p>
    </div>
  );
}

function TabPanel({ tab, turn, target, onPage, onOpenHit }: Omit<WorkbenchProps, "onTab">) {
  const sentAs = useMemo(() => new Map(turn?.sources.map((source) => [source.chunk_id, source.n]) ?? []), [turn]);
  const cited = useMemo(() => (turn === null || target === null ? new Set<number>() : citedPages(turn, target.paper.paper_id)), [turn, target]);
  if (tab === "page") {
    if (target === null) {
      return <EmptyPage hasTurn={turn !== null} />;
    }
    return (
      <Suspense fallback={<p role="status" className="bench-loading">Preparando el lector…</p>}>
        <ReaderPane target={target} citedPages={cited} onPage={onPage} />
      </Suspense>
    );
  }
  if (turn === null) {
    return <EmptyPage hasTurn={false} />;
  }
  if (tab === "retrieval") {
    return <RetrievalPane query={turn.searchQuery ?? turn.question} filters={turn.filters} sentAs={sentAs} onOpen={onOpenHit} />;
  }
  return <MeasuresPane turn={turn} />;
}

/** La mesa de trabajo junto a la conversación: la página citada, la recuperación y las medidas. */
export function Workbench({ turn, target, tab, onTab, onPage, onOpenHit }: WorkbenchProps) {
  const baseId = useId();

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0) {
      return;
    }
    event.preventDefault();
    const index = TABS.findIndex((candidate) => candidate.id === tab);
    const next = TABS[(index + step + TABS.length) % TABS.length];
    if (next !== undefined) {
      onTab(next.id);
      document.getElementById(`${baseId}-${next.id}`)?.focus();
    }
  }

  return (
    <div className="bench">
      <div className="tabs" role="tablist" aria-label="Mesa de trabajo" onKeyDown={onKeyDown}>
        {TABS.map((candidate) => (
          <button
            key={candidate.id}
            id={`${baseId}-${candidate.id}`}
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === candidate.id}
            aria-controls={`${baseId}-panel`}
            tabIndex={tab === candidate.id ? 0 : -1}
            onClick={() => onTab(candidate.id)}
          >
            <Icon name={candidate.icon} />
            {candidate.label}
          </button>
        ))}
      </div>
      <div className="bench__panel" id={`${baseId}-panel`} role="tabpanel" aria-labelledby={`${baseId}-${tab}`} tabIndex={0}>
        <TabPanel tab={tab} turn={turn} target={target} onPage={onPage} onOpenHit={onOpenHit} />
      </div>
    </div>
  );
}
