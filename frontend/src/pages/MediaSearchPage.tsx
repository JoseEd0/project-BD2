import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchStructure, runQuery, uploadMediaQuery } from "../api";
import FailureNotice from "../components/FailureNotice";
import SqlBlock from "../components/SqlBlock";
import StatusBar from "../components/StatusBar";
import TopBar from "../components/TopBar";
import MediaDetails from "../components/media/MediaDetails";
import QueryPicker from "../components/media/QueryPicker";
import SimilarityPanel from "../components/media/SimilarityPanel";
import { useSimilaritySearch } from "../hooks/useSimilaritySearch";
import type { Workspace } from "../hooks/useWorkspace";
import {
  DEFAULT_K,
  MAX_K,
  chosenTarget,
  clampK,
  defaultSettings,
  mediaCards,
  mediaTargets,
  queryFromCard,
  sampleSql,
  sharedFailure,
  similaritySql,
} from "../lib/mediaSearch";
import type { MediaCard, MediaQuery, MediaRequest, PanelSettings } from "../lib/mediaSearch";
import type { QueryFailure, SimilarityStructure } from "../types";

/** Filas de la tabla que se ofrecen como consulta. */
const SAMPLE_ROWS = 12;
/** Reintento si otra petición de la sesión estaba en curso al leer las filas de muestra. */
const BUSY_RETRY_MS = 150;
const PANEL_LABELS = ["A", "B"] as const;
/** Cómo se crea el índice de la demo si la tabla está pero no tiene uno. */
const SETUP_EXAMPLE =
  "CREATE INDEX idx_imagenes_hnsw ON imagenes USING HNSW (ruta) WITH (METRIC = cosine);";

interface MediaSearchPageProps {
  workspace: Workspace;
}

interface IndexTab {
  name: string;
  structure: SimilarityStructure;
}

function isSimilarity(structure: { kind: string }): structure is SimilarityStructure {
  return structure.kind === "hnsw" || structure.kind === "ivf";
}

function Setup() {
  return (
    <section className="searchintro">
      <p className="searchintro__lead">
        Ninguna columna tiene un índice HNSW o IVF. Se crean sobre la columna que guarda la
        ruta de cada imagen o audio; crearlo extrae SIFT o MFCC de cada archivo y entrena el
        vocabulario. La demo carga <code>imagenes</code> y <code>canciones</code> si sus
        datasets están descargados (ver <code>benchmarks/multimedia/README.md</code>).
      </p>
      <SqlBlock sql={SETUP_EXAMPLE} />
    </section>
  );
}

/**
 * La búsqueda multimedia de la Parte 4: el archivo de la consulta, subido o tomado de una
 * fila, y dos consultas `SIMILAR_TO` lado a lado —por omisión, los dos índices de la
 * columna, o el mismo con coseno y con euclidiana— con su SQL exacto y su galería. Todo va
 * por `POST /query` dentro de la sesión de la pestaña.
 */
export default function MediaSearchPage({ workspace }: MediaSearchPageProps) {
  const { tables, sessionId, exclusively } = workspace;
  const targets = useMemo(() => mediaTargets(tables), [tables]);
  const [targetKey, setTargetKey] = useState<string | null>(null);
  const target = chosenTarget(targets, targetKey);
  const [query, setQuery] = useState<MediaQuery | null>(null);
  const [k, setK] = useState(DEFAULT_K);
  const [settings, setSettings] = useState<PanelSettings[]>(() =>
    defaultSettings(target?.methods ?? []),
  );
  const [samples, setSamples] = useState<MediaCard[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadFailure, setUploadFailure] = useState<QueryFailure | null>(null);
  const [indexes, setIndexes] = useState<IndexTab[]>([]);
  const [indexError, setIndexError] = useState<string | null>(null);

  const methodsKey = target?.methods.join(",") ?? "";
  useEffect(() => {
    setSettings(defaultSettings(target?.methods ?? []));
    setQuery(null);
    // Solo al cambiar de columna o de índices, no en cada lectura del catálogo.
  }, [target?.key, methodsKey]);

  useEffect(() => {
    if (target === null) {
      setSamples([]);
      return;
    }
    let cancelled = false;
    const read = async () => {
      const ran = await exclusively(async () => {
        try {
          const response = await runQuery(sampleSql(target, SAMPLE_ROWS), sessionId);
          if (!cancelled) {
            setSamples(mediaCards(response, target));
          }
        } catch (error) {
          workspace.noteBackgroundFailure(error);
        }
      });
      if (!ran && !cancelled) {
        window.setTimeout(() => void read(), BUSY_RETRY_MS);
      }
    };
    void read();
    return () => {
      cancelled = true;
    };
  }, [target?.key, sessionId]);

  const tableRows = tables.find((table) => table.name === target?.table)?.row_count ?? 0;
  useEffect(() => {
    if (target === null) {
      setIndexes([]);
      return;
    }
    let cancelled = false;
    fetchStructure(target.table, sessionId)
      .then((structure) => {
        if (cancelled) {
          return;
        }
        setIndexes(
          structure.indexes.flatMap((index) =>
            index.column === target.column && isSimilarity(index.structure)
              ? [{ name: index.name, structure: index.structure }]
              : [],
          ),
        );
        setIndexError(null);
      })
      .catch((error: unknown) => {
        workspace.noteBackgroundFailure(error);
        if (!cancelled) {
          setIndexError("No se pudo leer la estructura del índice.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [target?.key, methodsKey, tableRows, sessionId]);

  const requests: (MediaRequest | null)[] = PANEL_LABELS.map((_, position) =>
    target === null || query === null || settings[position] === undefined
      ? null
      : { target, query, k, settings: settings[position] },
  );
  const live = useSimilaritySearch(requests, workspace);

  const upload = useCallback(
    async (file: File) => {
      setUploading(true);
      setUploadFailure(null);
      try {
        const uploaded = await uploadMediaQuery(file);
        setQuery({ path: uploaded.path, name: uploaded.name, kind: uploaded.media, category: null });
      } catch (error) {
        setUploadFailure(await workspace.acknowledgeFailure(error));
      } finally {
        setUploading(false);
      }
    },
    [workspace],
  );

  const pick = useCallback((card: MediaCard) => {
    const picked = queryFromCard(card);
    if (picked !== null) {
      setQuery(picked);
    }
  }, []);

  const changeSettings = useCallback((position: number, patch: Partial<PanelSettings>) => {
    setSettings((previous) =>
      previous.map((item, index) => (index === position ? { ...item, ...patch } : item)),
    );
  }, []);

  const common = sharedFailure(live.outcomes);
  const timed = live.outcomes.filter((outcome) => outcome?.elapsedMs != null);
  const elapsedMs =
    timed.length === 0 ? null : timed.reduce((sum, outcome) => sum + (outcome?.elapsedMs ?? 0), 0);

  return (
    <div className="workbench">
      <TopBar
        inTransaction={workspace.inTransaction}
        onRefresh={() => void workspace.refreshTables()}
        onToggleTheme={workspace.toggleTheme}
        page="multimedia"
        theme={workspace.theme}
      />
      <main className="textpage mediapage">
        {target === null ? (
          <Setup />
        ) : (
          <>
            <div className="mediabar">
              <label className="mediabar__field">
                <span className="mediabar__label">Columna</span>
                <select
                  className="mediabar__select"
                  onChange={(event) => setTargetKey(event.target.value)}
                  value={target.key}
                >
                  {targets.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.table}.{item.column} ({item.methods.join(" y ")})
                    </option>
                  ))}
                </select>
              </label>
              <label className="clause">
                <span className="clause__keyword">k</span>
                <span className="clause__operator">=</span>
                <input
                  aria-label="Filas que devuelve cada consulta"
                  className="clause__input clause__input--number"
                  max={MAX_K}
                  min={1}
                  onChange={(event) => setK(clampK(event.target.valueAsNumber))}
                  type="number"
                  value={k}
                />
              </label>
            </div>

            <QueryPicker
              failure={uploadFailure}
              onPick={pick}
              onUpload={(file) => void upload(file)}
              query={query}
              samples={samples}
              uploading={uploading}
            />

            {query === null ? (
              <section className="searchintro">
                <p className="searchintro__lead">
                  Elige un archivo para ver las {k} filas más parecidas con dos consultas
                  lado a lado, cada una con su SQL.
                </p>
              </section>
            ) : (
              <>
                {common !== null && (
                  <div className="textpage__notice textpage__notice--shared">
                    <FailureNotice failure={common} />
                  </div>
                )}
                <div className="rankpanels">
                  {PANEL_LABELS.map((label, position) => {
                    const request = requests[position];
                    return (
                      <SimilarityPanel
                        failureShownAbove={common !== null}
                        key={label}
                        label={`Consulta ${label}`}
                        methods={target.methods}
                        onOpenInConsole={workspace.openInConsole}
                        onSettings={(patch) => changeSettings(position, patch)}
                        outcome={live.outcomes[position] ?? null}
                        queryCategory={query.category}
                        settings={settings[position]}
                        sql={request === null ? "" : similaritySql(request)}
                        stale={live.searching}
                      />
                    );
                  })}
                </div>
                <MediaDetails
                  indexError={indexError}
                  indexes={indexes}
                  labels={PANEL_LABELS.map((label) => `la consulta ${label}`)}
                  outcomes={live.outcomes}
                />
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
