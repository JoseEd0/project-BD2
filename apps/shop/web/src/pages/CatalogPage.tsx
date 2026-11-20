import { useEffect } from "react";
import { useSearchParams } from "react-router";

import { usePhotoSearch, useTextSearch } from "../api/shop";
import type { ScoredProduct } from "../api/types";
import { FiltersPanel } from "../catalog/FiltersPanel";
import { activeFilterCount, filtersFromParams, NO_FILTERS, PARAM, priceRangeIsUpsideDown, withFilters, type CatalogFilters } from "../catalog/filters";
import { SUGGESTIONS, toSearchMode, type TextSearchMode } from "../catalog/searchMode";
import { ForYouPreview } from "../components/ForYouPreview";
import { ProblemNotice } from "../components/ProblemNotice";
import { ProductTile } from "../components/ProductTile";
import { StageTimings } from "../components/StageTimings";
import { APP } from "../identity";
import { usePhoto } from "../photo/PhotoContext";
import { PHOTO_REJECTIONS } from "../photo/prepare";

const PHOTO_MODE = "foto";
const EXACT_PARAM = "exacta";
const SCORE = new Intl.NumberFormat("es-PE", { maximumFractionDigits: 2 });

const HOW: Record<TextSearchMode | typeof PHOTO_MODE, string> = {
  palabras: "por las palabras de la ficha (BM25 sobre el índice invertido)",
  aspecto: "por cómo se ve la foto (CLIP: tu texto contra el HNSW de las fotos)",
  foto: "por tu foto (CLIP: la foto contra el HNSW de las fotos)",
};

function scoreLabel(mode: TextSearchMode | typeof PHOTO_MODE, score: number): string {
  return mode === "palabras" ? `BM25 ${SCORE.format(score)}` : `coseno ${SCORE.format(score)}`;
}

function Results({ items, mode }: { readonly items: readonly ScoredProduct[]; readonly mode: TextSearchMode | typeof PHOTO_MODE }) {
  return (
    <ul className="grid">
      {items.map((item) => (
        <li key={item.producto.id}>
          <ProductTile product={item.producto} score={scoreLabel(mode, item.puntaje)} />
        </li>
      ))}
    </ul>
  );
}

function NothingFound({
  query,
  mode,
  filters,
  onClearFilters,
}: {
  readonly query: string;
  readonly mode: TextSearchMode | typeof PHOTO_MODE;
  readonly filters: CatalogFilters;
  readonly onClearFilters: () => void;
}) {
  return (
    <div className="empty">
      <p className="empty-title">{mode === PHOTO_MODE ? "Ningún producto se parece a tu foto con estos filtros" : `Nada para «${query}»`}</p>
      <ul className="empty-tips">
        {mode !== PHOTO_MODE && <li>Las fichas están en inglés: prueba «strings» en lugar de «cuerdas».</li>}
        {mode === "palabras" && <li>Prueba «Cómo se ve»: busca por la foto y no por las palabras.</li>}
        {activeFilterCount(filters) > 0 && (
          <li>
            Los filtros pueden dejar fuera lo que buscas.{" "}
            <button type="button" className="button button-link" onClick={onClearFilters}>
              Quitar filtros
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}

function Landing({ onSuggest, filtered }: { readonly onSuggest: (query: string) => void; readonly filtered: boolean }) {
  return (
    <div className="landing">
      <section className="suggestions" aria-labelledby="suggestions-title">
        <h1 id="suggestions-title">¿Qué estás buscando?</h1>
        <p className="lede">
          Busca por palabras, por cómo se ve o con una foto. El catálogo está en inglés: estas ideas ya vienen traducidas.
        </p>
        {filtered && <p className="filter-pending">Los filtros que elegiste se aplican en cuanto busques algo.</p>}
        <ul className="suggestion-list">
          {SUGGESTIONS.map((suggestion) => (
            <li key={suggestion.query}>
              <button type="button" className="suggestion" onClick={() => onSuggest(suggestion.query)}>
                <span>{suggestion.label}</span>
                <span className="suggestion-query" lang="en">
                  {suggestion.query}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <ForYouPreview />
    </div>
  );
}

/** El catálogo: buscar por palabras, por cómo se ve o con una foto, con filtros de departamento y precio. */
export default function CatalogPage() {
  const [params, setParams] = useSearchParams();
  const filters = filtersFromParams(params);
  const query = params.get(PARAM.query)?.trim() ?? "";
  const isPhoto = params.get(PARAM.mode) === PHOTO_MODE;
  const textMode = toSearchMode(params.get(PARAM.mode));
  const mode = isPhoto ? PHOTO_MODE : textMode;
  const upsideDown = priceRangeIsUpsideDown(filters);
  const exact = params.get(EXACT_PARAM) === "1";
  const text = useTextSearch(textMode, isPhoto || upsideDown ? "" : query, filters, exact);
  const { photo, rejection, preparing, clear } = usePhoto();
  const photoSearch = usePhotoSearch();
  const { mutate: searchPhoto } = photoSearch;
  const filterKey = JSON.stringify(filters);

  useEffect(() => {
    if (isPhoto && photo !== null && !upsideDown) {
      searchPhoto({ photo: photo.blob, filters: JSON.parse(filterKey) as CatalogFilters });
    }
  }, [isPhoto, photo, filterKey, upsideDown, searchPhoto]);

  const changeFilters = (next: CatalogFilters) => setParams(withFilters(params, next));
  const suggest = (suggestion: string) => {
    const next = new URLSearchParams(params);
    next.set(PARAM.query, suggestion);
    next.set(PARAM.mode, "palabras");
    setParams(next);
  };

  const searchExactly = () => {
    const next = new URLSearchParams(params);
    next.set(EXACT_PARAM, "1");
    setParams(next);
  };

  const search = isPhoto ? photoSearch : text;
  const result = search.data;
  const searching = isPhoto ? photo !== null : query !== "";
  const title = isPhoto ? "Parecidos a tu foto" : query === "" ? null : `«${query}»`;

  return (
    <div className="catalog">
      <title>{title === null ? `${APP.name} · ${APP.tagline}` : `${title} · ${APP.name}`}</title>
      <FiltersPanel key={filterKey === JSON.stringify(NO_FILTERS) ? "none" : "some"} filters={filters} onChange={changeFilters} />
      <div className="catalog-main">
        {isPhoto && rejection !== null && (
          <ProblemNotice title={PHOTO_REJECTIONS[rejection].title} detail={PHOTO_REJECTIONS[rejection].detail} />
        )}
        {isPhoto && photo === null && rejection === null && (
          <div className="empty">
            <p className="empty-title">{preparing ? "Preparando tu foto…" : "Elige una foto para buscar"}</p>
            <p>Usa «Subir foto» o «Tomar foto» arriba: buscamos los productos que más se le parecen.</p>
          </div>
        )}
        {!searching ? (
          !isPhoto && <Landing onSuggest={suggest} filtered={activeFilterCount(filters) > 0} />
        ) : (
          <section className="results" aria-labelledby="results-title" aria-busy={search.isPending}>
            <header className="results-head">
              {isPhoto && photo !== null && <img className="results-photo" src={photo.url} alt="Tu foto" />}
              <div>
                <h1 id="results-title">{title}</h1>
                <p className="results-how">
                  {result === undefined
                    ? `Buscando ${HOW[mode]}…`
                    : `${result.data.products.length === 1 ? "1 producto" : `${result.data.products.length} productos`} ${HOW[mode]}.`}
                </p>
                {result?.data.correctedQuery != null && (
                  <p className="correction" role="status">
                    Mostrando resultados de <strong lang="en">{result.data.correctedQuery}</strong>.{" "}
                    <button type="button" className="button button-link" onClick={searchExactly}>
                      Buscar «{query}» tal cual
                    </button>
                  </p>
                )}
              </div>
              {isPhoto && (
                <button type="button" className="button button-link" onClick={clear}>
                  Quitar la foto
                </button>
              )}
              {result !== undefined && <StageTimings serverTiming={result.serverTiming} cache={result.cache} />}
            </header>
            {search.isError ? (
              <ProblemNotice
                title={search.error.title}
                detail={search.error.detail}
                onRetry={search.error.retryable ? () => (isPhoto ? photo && searchPhoto({ photo: photo.blob, filters }) : void text.refetch()) : undefined}
              />
            ) : result === undefined ? (
              <ul className="grid" aria-hidden="true">
                {Array.from({ length: 8 }, (_, index) => (
                  <li key={index} className="tile-skeleton" />
                ))}
              </ul>
            ) : result.data.products.length === 0 ? (
              <NothingFound query={query} mode={mode} filters={filters} onClearFilters={() => changeFilters(NO_FILTERS)} />
            ) : (
              <Results items={result.data.products} mode={mode} />
            )}
          </section>
        )}
      </div>
    </div>
  );
}
