import { useCallback, useMemo, useState } from "react";

import { runQuery } from "../api";
import FailureNotice from "../components/FailureNotice";
import InvertedIndexView from "../components/InvertedIndexView";
import StatusBar from "../components/StatusBar";
import TopBar from "../components/TopBar";
import ExecutionDetails from "../components/text/ExecutionDetails";
import IndexSetup from "../components/text/IndexSetup";
import RankingColumn from "../components/text/RankingColumn";
import SearchForm from "../components/text/SearchForm";
import type { EngineDefaults } from "../components/text/SearchForm";
import { useInvertedIndex } from "../hooks/useInvertedIndex";
import type { Workspace } from "../hooks/useWorkspace";
import {
  RANKING_METHODS,
  answeredOutcome,
  checkDraft,
  chosenFilterColumn,
  chosenTarget,
  failedOutcome,
  filterableColumns,
  indexableColumns,
  positionsByKey,
  resultFields,
  searchSql,
  searchTargets,
} from "../lib/textSearch";
import type { RankingMethod, RankingOutcome, SearchDraft } from "../lib/textSearch";
import { DEMO_CATALOG_SQL, TEXT_SEARCH_EXAMPLES } from "../textExamples";
import type { QueryFailure } from "../types";

const DEFAULT_LIMIT = 10;
const DEMO_TABLE = "catalogo";

const INITIAL_DRAFT: SearchDraft = {
  target: null,
  query: "",
  filterColumn: null,
  filterValue: "",
  k1: "",
  b: "",
  limit: DEFAULT_LIMIT,
};

interface TextSearchPageProps {
  workspace: Workspace;
}

function otherMethod(method: RankingMethod): RankingMethod {
  return method === "TF_IDF" ? "BM25" : "TF_IDF";
}

/**
 * La búsqueda de texto de la Parte 3: la misma consulta con TF-IDF y con BM25, lado a lado.
 * Todo va por `POST /query` con el SQL que se ve en «Cómo se ejecutó», dentro de la sesión
 * de la pestaña: si la consola tiene una transacción abierta, la búsqueda corre en ella.
 */
export default function TextSearchPage({ workspace }: TextSearchPageProps) {
  const { tables, sessionId, exclusively, running } = workspace;
  const [draft, setDraft] = useState<SearchDraft>(INITIAL_DRAFT);
  const [outcomes, setOutcomes] = useState<RankingOutcome[]>([]);
  const [defaults, setDefaults] = useState<EngineDefaults>({ k1: null, b: null });
  const [setupFailure, setSetupFailure] = useState<QueryFailure | null>(null);

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
  // El filtro va por la columna elegida o, mientras no se elija ninguna, por la marca: el
  // valor vacío es lo que lo deja sin aplicar.
  const filterColumn = chosenFilterColumn(filterColumns, draft.filterColumn, fields?.brand ?? null);
  const effective = useMemo(() => ({ ...draft, filterColumn }), [draft, filterColumn]);
  const check = checkDraft(effective, target, table);
  const index = useInvertedIndex(
    target,
    table === undefined ? "" : `${table.row_count}:${table.indexes.join(",")}`,
    sessionId,
    workspace.noteBackgroundFailure,
  );

  const terms = outcomes.find((outcome) => outcome.text !== null)?.text?.terms ?? [];
  const positions = useMemo(
    () => new Map(outcomes.map((outcome) => [outcome.method, positionsByKey(outcome.cards)])),
    [outcomes],
  );

  const change = useCallback((patch: Partial<SearchDraft>) => {
    setDraft((previous) => ({ ...previous, ...patch }));
  }, []);

  const search = useCallback(
    async (searched: SearchDraft) => {
      const ready = checkDraft(searched, target, table);
      if (ready.request === null || fields === null) {
        return;
      }
      const request = ready.request;
      await exclusively(async () => {
        const answered: RankingOutcome[] = [];
        for (const method of RANKING_METHODS) {
          const sql = searchSql({ ...request, method });
          try {
            const response = await runQuery(sql, sessionId);
            await workspace.acknowledge(response);
            answered.push(answeredOutcome(method, sql, response, fields));
            const used = response.text;
            if (method === "BM25" && used !== null) {
              setDefaults((previous) => ({
                k1: request.k1 === null ? used.k1 : previous.k1,
                b: request.b === null ? used.b : previous.b,
              }));
            }
          } catch (error) {
            answered.push(failedOutcome(method, sql, await workspace.acknowledgeFailure(error)));
          }
        }
        setOutcomes(answered);
      });
    },
    [exclusively, fields, sessionId, table, target, workspace],
  );

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

  const tryExample = useCallback(
    (query: string) => {
      setDraft((previous) => ({ ...previous, query }));
      void search({ ...effective, query });
    },
    [effective, search],
  );

  const hasDemoTable = tables.some((item) => item.name === DEMO_TABLE);
  const elapsedMs = outcomes.reduce((sum, outcome) => sum + (outcome.elapsedMs ?? 0), 0);

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
        <div className="textpage__content">
          <header className="textpage__intro">
            <h1 className="textpage__title">Búsqueda de texto</h1>
            <p className="textpage__lead">
              La misma consulta, ordenada con <strong>TF-IDF + coseno</strong> y con{" "}
              <strong>BM25</strong> sobre el mismo índice invertido. Las palabras resaltadas son las
              que coinciden con los términos ya analizados de la consulta.
            </p>
          </header>

          {setupFailure !== null && <FailureNotice failure={setupFailure} />}

          {targets.length === 0 ? (
            <IndexSetup
              candidates={indexableColumns(tables)}
              demoSql={hasDemoTable ? null : DEMO_CATALOG_SQL}
              onRun={(sql) => void runSetup(sql)}
              running={running}
            />
          ) : (
            <>
              <SearchForm
                defaults={defaults}
                draft={effective}
                examples={TEXT_SEARCH_EXAMPLES}
                filterColumns={filterColumns}
                onChange={change}
                onExample={tryExample}
                onSearch={() => void search(effective)}
                problem={check.problem}
                running={running}
                target={target}
                targets={targets}
              />

              {outcomes.length > 0 && (
                <>
                  <div className="terms">
                    <span className="terms__label">Términos analizados:</span>
                    {terms.length === 0 ? (
                      <span className="terms__none">ninguno: la consulta solo tiene stopwords</span>
                    ) : (
                      terms.map((term) => (
                        <code className="terms__term" key={term}>
                          {term}
                        </code>
                      ))
                    )}
                  </div>
                  <div className="rankings">
                    {outcomes.map((outcome) => (
                      <RankingColumn
                        key={outcome.method}
                        otherMethod={otherMethod(outcome.method)}
                        otherPositions={positions.get(otherMethod(outcome.method)) ?? new Map()}
                        outcome={outcome}
                        terms={terms}
                      />
                    ))}
                  </div>
                  <ExecutionDetails outcomes={outcomes} />
                </>
              )}
            </>
          )}

          {index.structure !== null && target !== null && (
            <section className="indexview">
              <h2 className="section-title">
                Índice {index.name} — INVERTED sobre {target.table}.{target.column}
              </h2>
              <InvertedIndexView inverted={index.structure} />
            </section>
          )}
          {index.error !== null && <div className="notice notice--error">{index.error}</div>}
        </div>
      </main>
      <StatusBar
        connected={workspace.connected}
        elapsedMs={outcomes.length > 0 ? elapsedMs : null}
        rowCount={null}
        tableCount={tables.length}
        totalRows={workspace.totalRows}
      />
    </div>
  );
}
