/**
 * La lógica de la página «Multimedia», sin React: qué columnas se pueden buscar, el SQL de
 * cada consulta y cómo se lee su resultado como galería. La página solo la pinta.
 *
 * Cada búsqueda es un `SIMILAR_TO` en SQL, el mismo que se escribiría en la consola:
 *
 *   SELECT id, titulo, categoria, ruta, SIMILARITY_SCORE() AS score
 *   FROM imagenes
 *   WHERE SIMILAR_TO('data/…/foto.jpg', k = 10)
 *   USING HNSW WITH METRIC = cosine
 *   ORDER BY score DESC;
 */

import type { CellValue, QueryFailure, QueryResponse, PlanInfo, TableInfo } from "../types";

export type VectorMethod = "HNSW" | "IVF";
export type VectorMetric = "cosine" | "euclidean" | "dot";
export type MediaKind = "image" | "audio";

export const VECTOR_METHODS: readonly VectorMethod[] = ["HNSW", "IVF"];
export const VECTOR_METRICS: readonly VectorMetric[] = ["cosine", "euclidean", "dot"];
export const METRIC_LABEL: Record<VectorMetric, string> = {
  cosine: "coseno",
  euclidean: "euclidiana",
  dot: "producto punto",
};
/** La opción del `WITH` que fija el esfuerzo de la búsqueda en cada índice. */
export const EFFORT_OPTION: Record<VectorMethod, string> = { HNSW: "ef_search", IVF: "probes" };
export const SCORE_ALIAS = "score";
export const DEFAULT_K = 10;
export const MAX_K = 100;
const SCORE_DIGITS = 3;

const IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"];
const AUDIO_EXTENSIONS = [".wav", ".mp3", ".flac", ".ogg"];
/** Nombres de columna que la galería usa como título y como clase, por orden de preferencia. */
const TITLE_COLUMNS = ["titulo", "title", "nombre", "name"];
const CATEGORY_COLUMNS = ["categoria", "category", "genero", "genre", "palabra", "word", "clase"];
const KEY_COLUMNS = ["id"];

/** Una columna con índice de similitud: dónde se puede buscar. */
export interface MediaTarget {
  key: string;
  table: string;
  column: string;
  methods: VectorMethod[];
  keyColumn: string | null;
  titleColumn: string | null;
  categoryColumn: string | null;
}

/** El archivo de la consulta: subido, o el de una fila de la tabla. */
export interface MediaQuery {
  path: string;
  name: string;
  kind: MediaKind;
  /** La clase de la fila elegida; `null` si el archivo se subió. */
  category: string | null;
}

/** Lo que cambia entre las dos consultas que se comparan. */
export interface PanelSettings {
  method: VectorMethod;
  metric: VectorMetric;
  /** `ef_search` o `probes` tal como se escribe; vacío, el del índice. */
  effort: string;
}

export interface MediaRequest {
  target: MediaTarget;
  query: MediaQuery;
  k: number;
  settings: PanelSettings;
}

/** Una fila del resultado, lista para la galería. */
export interface MediaCard {
  key: string;
  path: string;
  title: string | null;
  category: string | null;
  score: number | null;
}

export interface PanelOutcome {
  sql: string;
  cards: MediaCard[];
  plan: PlanInfo | null;
  failure: QueryFailure | null;
  elapsedMs: number | null;
}

export function mediaKindOf(path: string): MediaKind | null {
  const extension = path.slice(path.lastIndexOf(".")).toLowerCase();
  if (IMAGE_EXTENSIONS.includes(extension)) {
    return "image";
  }
  return AUDIO_EXTENSIONS.includes(extension) ? "audio" : null;
}

function firstColumn(table: TableInfo, names: readonly string[], except: string): string | null {
  const columns = table.columns.filter((column) => column.name !== except);
  for (const name of names) {
    const found = columns.find((column) => column.name.toLowerCase() === name);
    if (found !== undefined) {
      return found.name;
    }
  }
  return null;
}

/** Las columnas con índice HNSW o IVF de todas las tablas, con los métodos de cada una. */
export function mediaTargets(tables: readonly TableInfo[]): MediaTarget[] {
  return tables.flatMap((table) =>
    table.columns.flatMap((column) => {
      const methods = VECTOR_METHODS.filter((method) => column.indexed_with.includes(method));
      if (methods.length === 0) {
        return [];
      }
      return [
        {
          key: `${table.name}.${column.name}`,
          table: table.name,
          column: column.name,
          methods,
          keyColumn: table.primary_key ?? firstColumn(table, KEY_COLUMNS, column.name),
          titleColumn: firstColumn(table, TITLE_COLUMNS, column.name),
          categoryColumn: firstColumn(table, CATEGORY_COLUMNS, column.name),
        },
      ];
    }),
  );
}

export function chosenTarget(targets: readonly MediaTarget[], key: string | null): MediaTarget | null {
  return targets.find((target) => target.key === key) ?? targets[0] ?? null;
}

/** Las dos consultas de partida: los dos índices si la columna tiene los dos; si no, el
 * mismo índice con coseno y con euclidiana. */
export function defaultSettings(methods: readonly VectorMethod[]): [PanelSettings, PanelSettings] {
  const first = methods[0] ?? "HNSW";
  if (methods.length > 1) {
    return [
      { method: methods[0], metric: "cosine", effort: "" },
      { method: methods[1], metric: "cosine", effort: "" },
    ];
  }
  return [
    { method: first, metric: "cosine", effort: "" },
    { method: first, metric: "euclidean", effort: "" },
  ];
}

export function quoted(text: string): string {
  return `'${text.replaceAll("'", "''")}'`;
}

/** El esfuerzo pedido, si es un entero positivo; `null` si está vacío o no sirve. */
export function effortValue(effort: string): number | null {
  const value = Number(effort.trim());
  return effort.trim() === "" || !Number.isInteger(value) || value < 1 ? null : value;
}

function selectedColumns(target: MediaTarget): string[] {
  const named = [target.keyColumn, target.titleColumn, target.categoryColumn, target.column];
  return [...new Set(named.filter((name): name is string => name !== null))];
}

/** El SQL exacto de una de las dos consultas. */
export function similaritySql(request: MediaRequest): string {
  const { target, query, k, settings } = request;
  const options = [`METRIC = ${settings.metric}`];
  const effort = effortValue(settings.effort);
  if (effort !== null) {
    options.push(`${EFFORT_OPTION[settings.method]} = ${effort}`);
  }
  return [
    `SELECT ${selectedColumns(target).join(", ")}, SIMILARITY_SCORE() AS ${SCORE_ALIAS}`,
    `FROM ${target.table}`,
    `WHERE SIMILAR_TO(${quoted(query.path)}, k = ${k})`,
    `USING ${settings.method} WITH ${options.join(", ")}`,
    `ORDER BY ${SCORE_ALIAS} DESC;`,
  ].join("\n");
}

/** Las primeras filas de la tabla, para elegir una como consulta. */
export function sampleSql(target: MediaTarget, limit: number): string {
  return `SELECT ${selectedColumns(target).join(", ")}\nFROM ${target.table}\nLIMIT ${limit};`;
}

function text(value: CellValue | undefined): string | null {
  return value === null || value === undefined ? null : String(value);
}

/** Las filas de una respuesta como tarjetas de la galería. */
export function mediaCards(response: QueryResponse, target: MediaTarget): MediaCard[] {
  const position = (name: string | null) => (name === null ? -1 : response.columns.indexOf(name));
  const pathAt = position(target.column);
  const keyAt = position(target.keyColumn);
  const titleAt = position(target.titleColumn);
  const categoryAt = position(target.categoryColumn);
  const scoreAt = position(SCORE_ALIAS);
  return response.rows.map((row, index) => {
    const score = scoreAt < 0 ? null : row[scoreAt];
    return {
      key: text(row[keyAt]) ?? String(index),
      path: text(row[pathAt]) ?? "",
      title: titleAt < 0 ? null : text(row[titleAt]),
      category: categoryAt < 0 ? null : text(row[categoryAt]),
      score: typeof score === "number" ? score : null,
    };
  });
}

/** La fila de la tabla como consulta: su archivo y su clase. */
export function queryFromCard(card: MediaCard): MediaQuery | null {
  const kind = mediaKindOf(card.path);
  if (kind === null) {
    return null;
  }
  const name = card.title ?? card.path.slice(card.path.lastIndexOf("/") + 1);
  return { path: card.path, name, kind, category: card.category };
}

export function formatSimilarity(score: number): string {
  return score.toFixed(SCORE_DIGITS);
}

/** Qué fracción del mejor puntaje alcanza una fila, entre 0 y 1, para la barra. */
export function similarityShare(score: number | null, best: number | null): number {
  if (score === null || best === null || best <= 0) {
    return 0;
  }
  return Math.min(Math.max(score / best, 0), 1);
}

/** Cuántas de las filas comparten la clase de la consulta, si se conoce. */
export function sameCategoryCount(cards: readonly MediaCard[], category: string | null): number | null {
  if (category === null) {
    return null;
  }
  return cards.filter((card) => card.category === category).length;
}

/** El error que dieron las dos consultas, si es el mismo: se muestra una vez. */
export function sharedFailure(outcomes: readonly (PanelOutcome | null)[]): QueryFailure | null {
  const first = outcomes[0]?.failure ?? null;
  if (first === null || outcomes.length < 2) {
    return null;
  }
  return outcomes.every((outcome) => outcome?.failure?.error === first.error) ? first : null;
}

export function clampK(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_K;
  }
  return Math.min(Math.max(Math.round(value), 1), MAX_K);
}
