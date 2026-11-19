import { useCallback, useEffect, useRef, useState } from "react";

import { NO_FILTERS, type ResearchFilters, type SearchHit } from "../api/types";
import { Composer } from "../components/Composer";
import { EmptyConversation } from "../components/EmptyConversation";
import { Icon } from "../components/Icon";
import { TurnView } from "../components/TurnView";
import { Workbench, type WorkbenchTab } from "../components/Workbench";
import { APP } from "../identity";
import type { AnswerSource } from "../research/answerEvents";
import { targetOfHit, targetOfSource, type ReaderTarget } from "../research/readerTarget";
import type { Turn } from "../research/turns";
import { useMediaQuery } from "../research/useMediaQuery";
import { useResearch } from "../research/useResearch";

/** Debajo de este ancho la mesa de trabajo no cabe al lado: se abre como una hoja. */
const NARROW_LAYOUT = "(max-width: 63.99rem)";
/** Cada pregunta nueva sube al principio de la conversación: la respuesta crece debajo sin arrastrar la lectura. */
function useRevealNewTurn(turns: readonly Turn[]) {
  const pane = useRef<HTMLDivElement>(null);
  const lastLength = useRef(0);
  useEffect(() => {
    const asked = turns.length > lastLength.current;
    lastLength.current = turns.length;
    const latest = pane.current?.querySelector<HTMLElement>(".turn:last-child");
    if (asked && latest !== null && latest !== undefined) {
      latest.scrollIntoView({ block: "start" });
    }
  }, [turns.length]);
  return pane;
}

function Sheet({ open, onClose, children }: { readonly open: boolean; readonly onClose: () => void; readonly children: React.ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    if (node === null || typeof node.showModal !== "function") {
      return;
    }
    if (open && !node.open) {
      node.showModal();
    } else if (!open && node.open) {
      node.close();
    }
  }, [open]);
  return (
    <dialog ref={dialog} className="sheet" aria-label="Mesa de trabajo" onClose={onClose}>
      <div className="sheet__grip">
        <button type="button" className="button button--icon" aria-label="Cerrar" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      {open && children}
    </dialog>
  );
}

export default function ResearchPage() {
  const { turns, busy, ask, stop } = useResearch();
  const [filters, setFilters] = useState<ResearchFilters>(NO_FILTERS);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [target, setTarget] = useState<ReaderTarget | null>(null);
  const [tab, setTab] = useState<WorkbenchTab>("page");
  const [sheetOpen, setSheetOpen] = useState(false);
  const narrow = useMediaQuery(NARROW_LAYOUT);
  const pane = useRevealNewTurn(turns);

  const active = turns.find((turn) => turn.id === activeId) ?? turns.at(-1) ?? null;

  const askQuestion = useCallback(
    (question: string, scope: ResearchFilters = filters) => {
      setActiveId(null);
      setTarget(null);
      setTab("page");
      ask(question, scope);
    },
    [ask, filters],
  );

  const show = useCallback((turn: Turn, nextTab: WorkbenchTab) => {
    setActiveId(turn.id);
    setTab(nextTab);
    setSheetOpen(true);
  }, []);

  const onCite = useCallback(
    (turn: Turn, source: AnswerSource) => {
      setTarget(targetOfSource(source));
      show(turn, "page");
    },
    [show],
  );

  const onOpenHit = useCallback(
    (hit: SearchHit) => {
      const sent = active?.sources.find((source) => source.chunk_id === hit.chunk_id)?.n ?? null;
      setTarget(targetOfHit(hit, sent));
      setTab("page");
    },
    [active],
  );

  const onPage = useCallback((page: number) => setTarget((current) => (current === null ? null : { ...current, page })), []);

  const workbench = <Workbench turn={active} target={target} tab={tab} onTab={setTab} onPage={onPage} onOpenHit={onOpenHit} />;

  return (
    <main className="desk" id="contenido">
      <title>{APP.name}</title>
      <section className="conversation" aria-label="Conversación">
        <div className="conversation__scroll" ref={pane}>
          {turns.length === 0 ? (
            <EmptyConversation onAsk={(question) => askQuestion(question)} disabled={busy} />
          ) : (
            <div className="turns">
              <h1 className="visually-hidden">Conversación con los papers</h1>
              {turns.map((turn) => (
                <TurnView
                  key={turn.id}
                  turn={turn}
                  active={turn.id === active?.id}
                  openChunk={turn.id === active?.id ? (target?.chunkId ?? null) : null}
                  onCite={onCite}
                  onRetry={(failed) => askQuestion(failed.question, failed.filters)}
                  onInspect={show}
                />
              ))}
            </div>
          )}
        </div>
        <Composer busy={busy} filters={filters} onFiltersChange={setFilters} onAsk={askQuestion} onStop={stop} />
      </section>
      {narrow ? (
        <Sheet open={sheetOpen} onClose={() => setSheetOpen(false)}>
          {workbench}
        </Sheet>
      ) : (
        <aside className="workbench" aria-label="Mesa de trabajo">
          {workbench}
        </aside>
      )}
    </main>
  );
}
