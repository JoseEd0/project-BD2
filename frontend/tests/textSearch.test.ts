import assert from "node:assert/strict";
import { test } from "node:test";

import {
  checkDraft,
  chosenTarget,
  createIndexSql,
  defaultFilterColumn,
  filterCondition,
  formatScore,
  indexableColumns,
  optionalNumber,
  positionsByKey,
  resultCards,
  resultFields,
  scoreShare,
  searchSql,
  searchTargets,
  sharedFailure,
} from "../src/lib/textSearch.ts";
import type { RankingOutcome } from "../src/lib/textSearch.ts";
import type { ColumnInfo, TableInfo } from "../src/types.ts";

function column(name: string, type: string, indexedWith: string[] = []): ColumnInfo {
  return {
    name,
    type,
    length: null,
    nullable: true,
    primary_key: false,
    indexed_with: indexedWith,
    unique: false,
  };
}

const CATALOGO: TableInfo = {
  name: "catalogo",
  organization: "heap",
  primary_key: "id",
  row_count: 3,
  columns: [
    column("id", "STRING", ["BTREE"]),
    column("titulo", "STRING"),
    column("marca", "STRING"),
    column("precio", "FLOAT"),
    column("texto", "TEXT", ["INVERTED"]),
  ],
  indexes: ["idx_catalogo_texto"],
};

const NOTAS: TableInfo = {
  name: "notas",
  organization: "heap",
  primary_key: null,
  row_count: 0,
  columns: [column("id", "INT"), column("cuerpo", "TEXT")],
  indexes: [],
};

test("se busca en las columnas con índice invertido", () => {
  assert.deepEqual(searchTargets([CATALOGO, NOTAS]), [{ table: "catalogo", column: "texto" }]);
});

test("las columnas de texto sin índice invertido son candidatas a tenerlo", () => {
  assert.deepEqual(indexableColumns([NOTAS]), [{ table: "notas", column: "cuerpo" }]);
  const resumen = { ...NOTAS, columns: [column("resumen", "STRING"), column("cuerpo", "TEXT")] };
  assert.deepEqual(
    indexableColumns([resumen]).map((item) => item.column),
    ["cuerpo", "resumen"],
  );
  assert.equal(
    createIndexSql({ table: "notas", column: "cuerpo" }),
    "CREATE INDEX IF NOT EXISTS idx_notas_cuerpo\n  ON notas USING INVERTED (cuerpo);",
  );
});

test("la tarjeta toma título y marca por nombre y la clave primaria como clave", () => {
  assert.deepEqual(resultFields(CATALOGO, "texto"), {
    key: "id",
    title: "titulo",
    brand: "marca",
    text: "texto",
  });
  assert.deepEqual(resultFields(NOTAS, "cuerpo"), {
    key: "id",
    title: null,
    brand: null,
    text: "cuerpo",
  });
});

test("el filtro escribe el literal según el tipo y escapa las comillas", () => {
  const [, , marca, precio] = CATALOGO.columns;
  assert.equal(filterCondition(marca, "L'Oréal"), "marca = 'L''Oréal'");
  assert.equal(filterCondition(precio, "19.9"), "precio = 19.9");
  assert.equal(filterCondition(precio, "barato"), undefined);
  assert.equal(filterCondition(marca, ""), null);
});

test("la consulta lleva MATCH, sus condiciones, el ranking y sus parámetros", () => {
  const request = {
    target: { table: "catalogo", column: "texto" },
    query: "zapatillas d'running",
    conditions: ["marca = 'PUMA'", "precio = 59.9"],
    k1: 2,
    b: null,
    limit: 10,
  };
  assert.equal(
    searchSql({ ...request, method: "BM25" }),
    [
      "SELECT *, SCORE() AS puntaje",
      "FROM catalogo",
      "WHERE MATCH(texto, 'zapatillas d''running')",
      "  AND marca = 'PUMA'",
      "  AND precio = 59.9",
      "USING BM25 WITH (k1 = 2)",
      "ORDER BY puntaje DESC",
      "LIMIT 10;",
    ].join("\n"),
  );
  assert.ok(
    searchSql({ ...request, method: "TF_IDF", conditions: [] }).includes(
      "WHERE MATCH(texto, 'zapatillas d''running')\nUSING TF_IDF\n",
    ),
  );
  assert.ok(searchSql({ ...request, method: "BM25", k1: null }).includes("USING BM25\nORDER BY"));
});

test("un número opcional distingue vacío de inválido", () => {
  assert.equal(optionalNumber(""), null);
  assert.equal(optionalNumber("0.75"), 0.75);
  assert.equal(optionalNumber("x"), undefined);
});

test("las filas se leen como tarjetas y sus posiciones se buscan por clave", () => {
  const fields = resultFields(CATALOGO, "texto");
  const cards = resultCards(
    ["id", "titulo", "marca", "precio", "texto", "puntaje"],
    [
      ["B1", "Funda", null, 9.5, "Funda de piel", 3.2],
      ["B2", "Cable", "Anker", 5, "Cable USB", 1.0],
    ],
    fields,
  );
  assert.deepEqual(cards[0], {
    key: "B1",
    title: "Funda",
    brand: null,
    text: "Funda de piel",
    score: 3.2,
  });
  const positions = positionsByKey(cards);
  assert.equal(positions.get("B2"), 2);
  assert.equal(positions.get("B9"), undefined);
});

test("se busca en la columna elegida mientras exista, y si no en la primera", () => {
  const targets = [
    { table: "catalogo", column: "texto" },
    { table: "notas", column: "cuerpo" },
  ];
  assert.deepEqual(chosenTarget(targets, "notas.cuerpo"), targets[1]);
  assert.deepEqual(chosenTarget(targets, "borrada.texto"), targets[0]);
  assert.equal(chosenTarget([], null), null);
});

test("el borrador dice qué le falta antes de consultar", () => {
  const target = { table: "catalogo", column: "texto" };
  const draft = {
    target: "catalogo.texto",
    query: " funda ",
    filters: [
      { id: 1, column: "precio", value: "" },
      { id: 2, column: "marca", value: "PUMA" },
    ],
    k1: "",
    b: "0.5",
    limit: 10,
  };
  assert.deepEqual(checkDraft(draft, target, CATALOGO), {
    request: {
      target,
      query: "funda",
      conditions: ["marca = 'PUMA'"],
      k1: null,
      b: 0.5,
      limit: 10,
    },
    problem: null,
  });
  assert.equal(
    checkDraft({ ...draft, query: "  " }, target, CATALOGO).problem,
    "Escribe qué buscar.",
  );
  assert.match(checkDraft({ ...draft, k1: "uno" }, target, CATALOGO).problem ?? "", /k1 y b/);
  const priced = { ...draft, filters: [{ id: 1, column: "precio", value: "barato" }] };
  assert.match(checkDraft(priced, target, CATALOGO).problem ?? "", /«precio» necesita un número/);
  assert.ok(checkDraft(draft, null, undefined).problem !== null);
});

test("una condición nueva empieza por la marca si la tabla tiene, y si no por la primera", () => {
  const columns = CATALOGO.columns.slice(0, 4);
  assert.equal(defaultFilterColumn(columns, "marca"), "marca");
  assert.equal(defaultFilterColumn(columns, null), "id");
  assert.equal(defaultFilterColumn(columns, "borrada"), "id");
  assert.equal(defaultFilterColumn([], "marca"), null);
});

test("el puntaje se escribe con los decimales de su ranking y su barra va de 0 a 1", () => {
  assert.equal(formatScore(0.52451, "TF_IDF"), "0.525");
  assert.equal(formatScore(14.2329, "BM25"), "14.23");
  assert.equal(formatScore(3, "BM25"), "3.00");
  assert.equal(scoreShare(7, 14), 0.5);
  assert.equal(scoreShare(14, 14), 1);
  assert.equal(scoreShare(null, 14), 0);
  assert.equal(scoreShare(5, null), 0);
  assert.equal(scoreShare(-1, 14), 0);
});

function outcomeFailing(error: string | null): RankingOutcome {
  const failure =
    error === null
      ? null
      : { error, kind: "BooleanQueryError", line: null, column: null, in_transaction: false };
  return { method: "BM25", sql: "", cards: [], elapsedMs: null, plan: null, text: null, failure };
}

test("un error se muestra una sola vez solo si todos los rankings fallaron por lo mismo", () => {
  const both = [outcomeFailing("solo excluye"), outcomeFailing("solo excluye")];
  assert.equal(sharedFailure(both)?.error, "solo excluye");
  assert.equal(sharedFailure([outcomeFailing("solo excluye"), outcomeFailing(null)]), null);
  assert.equal(sharedFailure([outcomeFailing(null), outcomeFailing("k1 negativo")]), null);
  assert.equal(sharedFailure([outcomeFailing("a"), outcomeFailing("b")]), null);
  assert.equal(sharedFailure([]), null);
});
