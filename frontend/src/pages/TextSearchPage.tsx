import { useCallback, useMemo, useRef, useState } from "react";

import { runQuery } from "../api";
import FailureNotice from "../components/FailureNotice";
import StatusBar from "../components/StatusBar";
import TopBar from "../components/TopBar";
import ClauseBar from "../components/text/ClauseBar";
import DetailTabs from "../components/text/DetailTabs";
import IndexSetup from "../components/text/IndexSetup";
import RankingPanel from "../components/text/RankingPanel";
import SearchBar from "../components/text/SearchBar";
import SearchIntro from "../components/text/SearchIntro";
import { useInvertedIndex } from "../hooks/useInvertedIndex";
import { useLiveSearch } from "../hooks/useLiveSearch";
import type { ReadySearch } from "../hooks/useLiveSearch";
import type { Workspace } from "../hooks/useWorkspace";
import {
  RANKING_METHODS,
  checkDraft,
  chosenTarget,
  defaultFilterColumn,
  filterableColumns,
  indexableColumns,
  positionsByKey,
  resultFields,
  searchSql,
  searchTargets,
  sharedFailure,
} from "../lib/textSearch";
import type {
  FilterDraft,
  RankingMethod,
  SearchDraft,
  SearchRequest,
  SearchTarget,
} from "../lib/textSearch";
import { DEMO_CATALOG_SQL, TEXT_SEARCH_EXAMPLES } from "../textExamples";
import type { QueryFailure, TableInfo } from "../types";

const DEFAULT_LIMIT = 10;
const DEMO_TABLE = "catalogo";
/** El texto de muestra del `MATCH` en la consulta que se ve antes de escribir. */
const SAMPLE_QUERY = "lo que escribas";

const INITIAL_DRAFT: SearchDraft = {
  target: null,
  query: "",
  filters: [],
  k1: "",
  b: "",
  limit: DEFAULT_LIMIT,
};

interface TextSearchPageProps {
  workspace: Workspace;
}

/** La consulta de BM25 con lo que ya está escrito y un texto de muestra en el `MATCH`. */
function previewSql(draft: SearchDraft, target: SearchTarget, table: TableInfo | undefined): string {
  const sample = checkDraft({ ...draft, query: SAMPLE_QUERY }, target, table).request ?? {
    target,
    query: SAMPLE_QUERY,
    conditions: [],
    k1: null,
    b: null,
    limit: draft.limit,
  };
  return searchSql({ ...sample, method: "BM25" });
}

function otherMethod(method: RankingMethod): RankingMethod {
  return method === "TF_IDF" ? "BM25" : "TF_IDF";
}

/**
 * La búsqueda de texto de la Parte 3: lo escrito es el texto de un `MATCH`, y cada búsqueda
 * son dos consultas SQL —una con TF-IDF y otra con BM25— que se ven lado a lado con su
 * resultado. Todo va por `POST /query`, dentro de la sesión de la pestaña: si la consola
 * tiene una transacción abierta, la búsqueda corre en ella.
 */
export default function TextSearchPage({ workspace }: TextSearchPageProps) {
  const { tables, sessionId, exclusively, running } = workspace;
  const [draft, setDraft] = useState<SearchDraft>(INITIAL_DRAFT);
  const [setupFailure, setSetupFailure] = useState<QueryFailure | null>(null);
  const nextFilterId = useRef(0);
  // Mientras una condición o un parámetro están a medio escribir, el SQL a la vista sigue
  // siendo el último que se pudo ejecutar.
  const lastRequest = useRef<Omit<SearchRequest, "method"> | null>(null);

  const targets = useMemo(() => searchTargets(tables), [tables]);
  const target = chosenTarget(targets, draft.target);
  const table = tables.find((item) => item.name === target?.table);
  const fields = useMemo(
    () => (table === undefined || target === null ? null : resultFields(table, target.column)),
    [table, target],
  );
  const filterColumns = useMemo(
    () => (table === undefined || target === null ? [] : filterableColumns(table, target.column)),
    [table, target],
  );
  const check = checkDraft(draft, target, table);
  if (check.request !== null) {
    lastRequest.current = check.request;
  }
  const typed = draft.query.trim().length > 0;
  const shownRequest = typed ? lastRequest.current : null;
  const ready: ReadySearch | null =
    check.request === null || fields === null ? null : { request: check.request, fields };

  const live = useLiveSearch(ready, workspace);
  const index = useInvertedIndex(
    target,
    table === undefined ? "" : `${table.row_count}:${table.indexes.join(",")}`,
    sessionId,
    workspace.noteBackgroundFailure,
  );

  const terms = live.outcomes.find((outcome) => outcome.text !== null)?.text?.terms ?? null;
  const positions = useMemo(
    () => new Map(live.outcomes.map((outcome) => [outcome.method, positionsByKey(outcome.cards)])),
    [live.outcomes],
  );

  const change = useCallback((patch: Partial<SearchDraft>) => {
    setDraft((previous) => ({ ...previous, ...patch }));
  }, []);

  const addFilter = useCallback(() => {
    const column = defaultFilterColumn(filterColumns, fields?.brand ?? null);
    if (column === null) {
      return;
    }
    nextFilterId.current += 1;
    const added: FilterDraft = { id: nextFilterId.current, column, value: "" };
    setDraft((previous) => ({ ...previous, filters: [...previous.filters, added] }));
  }, [fields, filterColumns]);

  const changeFilter = useCallback((id: number, patch: Partial<FilterDraft>) => {
    setDraft((previous) => ({
      ...previous,
      filters: previous.filters.map((filter) => (filter.id === id ? { ...filter, ...patch } : filter)),
    }));
  }, []);

  const removeFilter = useCallback((id: number) => {
    setDraft((previous) => ({
      ...previous,
      filters: previous.filters.filter((filter) => filter.id !== id),
    }));
  }, []);

  const runSetup = useCallback(
    async (sql: string) => {
      await exclusively(async () => {
        setSetupFailure(null);
        try {
          await workspace.acknowledge(await runQuery(sql, sessionId));
        } catch (error) {
          setSetupFailure(await workspace.acknowledgeFailure(error));
        }
      });
    },
    [exclusively, sessionId, workspace],
  );

  const commonFailure = sharedFailure(live.outcomes);
  const hasPlan = live.outcomes.some((outcome) => outcome.plan !== null);
  const hasDemoTable = tables.some((item) => item.name === DEMO_TABLE);
  const timed = live.outcomes.filter((outcome) => outcome.elapsedMs !== null);
  const elapsedMs =
    timed.length === 0 ? null : timed.reduce((sum, outcome) => sum + (outcome.elapsedMs ?? 0), 0);

  return (
    <div className="workbench">
      <TopBar
        inTransaction={workspace.inTransaction}
        onRefresh={() => void workspace.refreshTables()}
        onToggleTheme={workspace.toggleTheme}
        page="texto"
        theme={workspace.theme}
      />
      <main className="textpage">
        {setupFailure !== null && (
          <div className="textpage__notice">
            <FailureNotice failure={setupFailure} />
          </div>
        )}

        {target === null ? (
          <IndexSetup
            candidates={indexableColumns(tables)}
            demoSql={hasDemoTable ? null : DEMO_CATALOG_SQL}
            onRun={(sql) => void runSetup(sql)}
            running={running}
          />
        ) : (
          <>
            <SearchBar
              onQuery={(query) => change({ query })}
              onSubmit={live.searchNow}
              onTarget={(key) => change({ target: key, filters: [] })}
              query={draft.query}
              searching={live.searching}
              target={target}
              targets={targets}
              terms={terms}
            >
              <ClauseBar
                b={draft.b}
                engine={live.bm25}
                filterColumns={filterColumns}
                filters={draft.filters}
                k1={draft.k1}
                limit={draft.limit}
                onAddFilter={addFilter}
                onChangeFilter={changeFilter}
                onLimit={(limit) => change({ limit })}
                onParameters={change}
                onRemoveFilter={removeFilter}
                problem={typed ? check.problem : null}
              />
            </SearchBar>

            {shownRequest === null ? (
              <SearchIntro
                examples={TEXT_SEARCH_EXAMPLES}
                onExample={(query) => change({ query })}
                sql={previewSql(draft, target, table)}
                target={target}
              />
            ) : (
              <>
                {commonFailure !== null && (
                  <div className="textpage__notice textpage__notice--shared">
                    <FailureNotice failure={commonFailure} />
                  </div>
                )}
                <div className="rankpanels">
                  {RANKING_METHODS.map((method) => (
                    <RankingPanel
                      key={method}
                      method={method}
                      onOpenInConsole={workspace.openInConsole}
                      otherMethod={otherMethod(method)}
                      otherPositions={positions.get(otherMethod(method)) ?? new Map()}
                      outcome={live.outcomes.find((outcome) => outcome.method === method) ?? null}
                      sql={searchSql({ ...shownRequest, method })}
                      failureShownAbove={commonFailure !== null}
                      stale={live.searching}
                      terms={terms ?? []}
                    />
                  ))}
                </div>
                {hasPlan && (
                  <DetailTabs
                    index={index.structure}
                    indexError={index.error}
                    indexName={index.name}
                    outcomes={live.outcomes}
                  />
                )}
              </>
            )}
          </>
        )}
      </main>
      <StatusBar
        connected={workspace.connected}
        elapsedMs={elapsedMs}
        rowCount={null}
        tableCount={tables.length}
        totalRows={workspace.totalRows}
      />
    </div>
  );
}
