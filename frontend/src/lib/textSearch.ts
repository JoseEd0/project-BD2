/**
 * Lo que la página «Texto» necesita saber de las tablas y el SQL que escribe. Todo pasa por
 * `POST /query`: la página no tiene un camino propio hacia el motor, solo arma la misma
 * consulta que se escribiría a mano en la consola.
 */

import type {
  CellValue,
  ColumnInfo,
  PlanInfo,
  QueryFailure,
  QueryResponse,
  TableInfo,
  TextSearchInfo,
} from "../types";

export type RankingMethod = "TF_IDF" | "BM25";

export const RANKING_METHODS: readonly RankingMethod[] = ["TF_IDF", "BM25"];

export const RANKING_LABEL: Record<RankingMethod, string> = {
  TF_IDF: "TF-IDF + coseno",
  BM25: "BM25",
};

export const INVERTED_METHOD = "INVERTED";
export const SCORE_ALIAS = "puntaje";
const TEXT_TYPES = new Set(["STRING", "TEXT"]);
const NUMERIC_TYPES = new Set(["INT", "FLOAT"]);
const TITLE_NAMES = ["titulo", "title", "nombre", "name"];
const BRAND_NAMES = ["marca", "brand"];

/** Una columna de texto con índice invertido: donde se puede buscar con `MATCH`. */
export interface SearchTarget {
  table: string;
  column: string;
}

/** Qué columna del resultado hace de clave, de título y de marca en cada tarjeta. */
export interface ResultFields {
  key: string;
  title: string | null;
  brand: string | null;
  text: string;
}

/** Todo lo que decide la consulta de una búsqueda. `filter` es la condición relacional que
 * acompaña al `MATCH`, ya escrita; `k1` y `b` en `null` dejan los del motor. */
export interface SearchRequest {
  target: SearchTarget;
  query: string;
  method: RankingMethod;
  filter: string | null;
  k1: number | null;
  b: number | null;
  limit: number;
}

export function targetKey(target: SearchTarget): string {
  return `${target.table}.${target.column}`;
}

function isTextColumn(column: ColumnInfo): boolean {
  return TEXT_TYPES.has(column.type);
}

/** Las columnas de texto con índice invertido, en el orden del catálogo. */
export function searchTargets(tables: readonly TableInfo[]): SearchTarget[] {
  return tables.flatMap((table) =>
    table.columns
      .filter((column) => column.indexed_with.includes(INVERTED_METHOD))
      .map((column) => ({ table: table.name, column: column.name })),
  );
}

/** Las columnas de texto que todavía no tienen índice invertido: candidatas a tenerlo. Las
 * `TEXT` van primero, porque son las de documentos largos; detrás, las `VARCHAR`. */
export function indexableColumns(tables: readonly TableInfo[]): SearchTarget[] {
  const candidates = tables.flatMap((table) =>
    table.columns
      .filter((column) => isTextColumn(column) && !column.indexed_with.includes(INVERTED_METHOD))
      .map((column) => ({ table: table.name, column: column.name, long: column.type === "TEXT" })),
  );
  return [
    ...candidates.filter((item) => item.long),
    ...candidates.filter((item) => !item.long),
  ].map(({ table, column }) => ({ table, column }));
}

export function createIndexSql(target: SearchTarget): string {
  return `CREATE INDEX IF NOT EXISTS idx_${target.table}_${target.column}\n  ON ${target.table} USING INVERTED (${target.column});`;
}

function namedColumn(table: TableInfo, names: readonly string[], skip: string): string | null {
  const found = table.columns.find(
    (column) => names.includes(column.name.toLowerCase()) && column.name !== skip,
  );
  return found?.name ?? null;
}

/** Cómo leer una fila como tarjeta: por nombre si la tabla tiene `titulo` o `marca`, y si
 * no, la primera columna de texto que no es la buscada. */
export function resultFields(table: TableInfo, searched: string): ResultFields {
  const firstText = table.columns.find(
    (column) => isTextColumn(column) && column.name !== searched,
  );
  return {
    key: table.primary_key ?? table.columns[0].name,
    title: namedColumn(table, TITLE_NAMES, searched) ?? firstText?.name ?? null,
    brand: namedColumn(table, BRAND_NAMES, searched),
    text: searched,
  };
}

/** Las columnas por las que tiene sentido filtrar: todas menos la buscada y las que no se
 * comparan con un literal escrito a mano. */
export function filterableColumns(table: TableInfo, searched: string): ColumnInfo[] {
  return table.columns.filter(
    (column) =>
      column.name !== searched && (isTextColumn(column) || NUMERIC_TYPES.has(column.type)),
  );
}

export function quoteText(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

/** La condición `columna = valor` con el literal que pide el tipo de la columna: `null` sin
 * valor y `undefined` si la columna es numérica y el valor no es un número. */
export function filterCondition(column: ColumnInfo, value: string): string | null | undefined {
  if (value.length === 0) {
    return null;
  }
  if (!NUMERIC_TYPES.has(column.type)) {
    return `${column.name} = ${quoteText(value)}`;
  }
  const number = optionalNumber(value);
  return number === null || number === undefined ? undefined : `${column.name} = ${number}`;
}

function rankingClause(request: SearchRequest): string {
  if (request.method === "TF_IDF") {
    return "USING TF_IDF";
  }
  const parameters = [
    request.k1 === null ? null : `k1 = ${request.k1}`,
    request.b === null ? null : `b = ${request.b}`,
  ].filter((item): item is string => item !== null);
  return parameters.length === 0 ? "USING BM25" : `USING BM25 WITH (${parameters.join(", ")})`;
}

/** La consulta de una búsqueda, tal como se escribiría en la consola. */
export function searchSql(request: SearchRequest): string {
  const { table, column } = request.target;
  const match = `MATCH(${column}, ${quoteText(request.query)})`;
  const where = request.filter === null ? match : `${match}\n  AND ${request.filter}`;
  return [
    `SELECT *, SCORE() AS ${SCORE_ALIAS}`,
    `FROM ${table}`,
    `WHERE ${where}`,
    rankingClause(request),
    `ORDER BY ${SCORE_ALIAS} DESC`,
    `LIMIT ${request.limit};`,
  ].join("\n");
}

/** Lee un número opcional de un campo del formulario: vacío es `null` y lo que no es un
 * número, `undefined`. */
export function optionalNumber(text: string): number | null | undefined {
  if (text.trim().length === 0) {
    return null;
  }
  const value = Number(text);
  return Number.isFinite(value) ? value : undefined;
}

/** Una fila del resultado leída como tarjeta. */
export interface ResultCard {
  key: string;
  title: string | null;
  brand: string | null;
  text: string;
  score: number | null;
}

function textOf(value: CellValue | undefined): string | null {
  return value === undefined || value === null ? null : String(value);
}

/** Las filas de una búsqueda como tarjetas, en el orden en que llegaron. */
export function resultCards(
  columns: readonly string[],
  rows: readonly CellValue[][],
  fields: ResultFields,
): ResultCard[] {
  const at = (name: string | null) => (name === null ? -1 : columns.indexOf(name));
  const [key, title, brand, text, score] = [
    at(fields.key),
    at(fields.title),
    at(fields.brand),
    at(fields.text),
    at(SCORE_ALIAS),
  ];
  return rows.map((row) => ({
    key: textOf(row[key]) ?? "",
    title: textOf(row[title]),
    brand: textOf(row[brand]),
    text: textOf(row[text]) ?? "",
    score: typeof row[score] === "number" ? row[score] : null,
  }));
}

/** Lo que devolvió uno de los dos rankings: sus tarjetas y su plan, o el error del motor. */
export interface RankingOutcome {
  method: RankingMethod;
  sql: string;
  cards: ResultCard[];
  elapsedMs: number | null;
  plan: PlanInfo | null;
  text: TextSearchInfo | null;
  failure: QueryFailure | null;
}

export function answeredOutcome(
  method: RankingMethod,
  sql: string,
  response: QueryResponse,
  fields: ResultFields,
): RankingOutcome {
  return {
    method,
    sql,
    cards: resultCards(response.columns, response.rows, fields),
    elapsedMs: response.elapsed_ms,
    plan: response.plan,
    text: response.text,
    failure: null,
  };
}

export function failedOutcome(
  method: RankingMethod,
  sql: string,
  failure: QueryFailure,
): RankingOutcome {
  return { method, sql, cards: [], elapsedMs: null, plan: null, text: null, failure };
}

/** La columna del filtro: la elegida si es de las que se pueden filtrar; si no, la
 * preferida (la marca, si la tabla tiene) o la primera. */
export function chosenFilterColumn(
  columns: readonly ColumnInfo[],
  chosen: string | null,
  preferred: string | null,
): string | null {
  const names = columns.map((column) => column.name);
  const found = [chosen, preferred].find((name) => name !== null && names.includes(name));
  return found ?? names[0] ?? null;
}

/** El objetivo elegido si sigue existiendo; si no, el primero. */
export function chosenTarget(
  targets: readonly SearchTarget[],
  key: string | null,
): SearchTarget | null {
  return targets.find((target) => targetKey(target) === key) ?? targets[0] ?? null;
}

/** Posición (desde 1) de cada tarjeta de un ranking, por su clave. */
export function positionsByKey(cards: readonly ResultCard[]): Map<string, number> {
  return new Map(cards.map((card, index) => [card.key, index + 1]));
}

/** Lo que el formulario tiene escrito, tal cual: los números todavía como texto. */
export interface SearchDraft {
  target: string | null;
  query: string;
  filterColumn: string | null;
  filterValue: string;
  k1: string;
  b: string;
  limit: number;
}

/** El borrador listo para consultar con cualquiera de los dos rankings, o qué le falta. */
export type DraftCheck =
  { request: Omit<SearchRequest, "method">; problem: null } | { request: null; problem: string };

function failed(problem: string): DraftCheck {
  return { request: null, problem };
}

/** Comprueba el borrador contra la tabla en la que busca. Los rangos de `k1` y `b` no se
 * comprueban aquí: los valida el motor, y su error se ve en la columna de BM25. */
export function checkDraft(
  draft: SearchDraft,
  target: SearchTarget | null,
  table: TableInfo | undefined,
): DraftCheck {
  if (target === null || table === undefined) {
    return failed("Elige la columna en la que buscar.");
  }
  if (draft.query.trim().length === 0) {
    return failed("Escribe qué buscar.");
  }
  const k1 = optionalNumber(draft.k1);
  const b = optionalNumber(draft.b);
  if (k1 === undefined || b === undefined) {
    return failed("k1 y b tienen que ser números, o quedar vacíos para usar los del motor.");
  }
  const column = table.columns.find((item) => item.name === draft.filterColumn);
  const filter = column === undefined ? null : filterCondition(column, draft.filterValue);
  if (filter === undefined) {
    return failed(`El filtro sobre «${draft.filterColumn}» necesita un número.`);
  }
  return {
    request: { target, query: draft.query.trim(), filter, k1, b, limit: draft.limit },
    problem: null,
  };
}
