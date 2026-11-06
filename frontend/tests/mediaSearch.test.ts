import assert from "node:assert/strict";
import { test } from "node:test";

import {
  chosenTarget,
  clampK,
  defaultSettings,
  effortValue,
  mediaCards,
  mediaKindOf,
  mediaTargets,
  queryFromCard,
  quoted,
  sameCategoryCount,
  sampleSql,
  sharedFailure,
  similarityShare,
  similaritySql,
} from "../src/lib/mediaSearch.ts";
import type { MediaRequest, PanelOutcome } from "../src/lib/mediaSearch.ts";
import type { ColumnInfo, QueryResponse, TableInfo } from "../src/types.ts";

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

const IMAGENES: TableInfo = {
  name: "imagenes",
  organization: "heap",
  primary_key: "id",
  row_count: 1000,
  columns: [
    column("id", "INT", ["BTREE"]),
    column("ruta", "VARCHAR", ["HNSW", "IVF"]),
    column("titulo", "TEXT"),
    column("categoria", "VARCHAR"),
  ],
  indexes: ["pk_imagenes", "idx_hnsw", "idx_ivf"],
};

const CANCIONES: TableInfo = {
  name: "canciones",
  organization: "heap",
  primary_key: null,
  row_count: 20,
  columns: [column("ruta", "VARCHAR", ["IVF"]), column("titulo", "TEXT"), column("genero", "VARCHAR")],
  indexes: ["idx_ivf"],
};

const CLIENTES: TableInfo = {
  name: "clientes",
  organization: "heap",
  primary_key: "id",
  row_count: 10,
  columns: [column("id", "INT"), column("ciudad", "VARCHAR", ["HASH"])],
  indexes: ["idx_ciudad"],
};

const [IMAGES, SONGS] = mediaTargets([IMAGENES, CLIENTES, CANCIONES]);

const REQUEST: MediaRequest = {
  target: IMAGES,
  query: { path: "data/datasets/abo/c8.jpg", name: "c8.jpg", kind: "image", category: "SHOES" },
  k: 10,
  settings: { method: "HNSW", metric: "cosine", effort: "" },
};

function response(columns: string[], rows: QueryResponse["rows"]): QueryResponse {
  return {
    statements: [],
    columns,
    rows,
    total_rows: rows.length,
    plan: null,
    message: "",
    affected_rows: 0,
    elapsed_ms: 1,
    in_transaction: false,
    spatial: null,
    text: null,
    similarity: null,
  };
}

test("only columns with an HNSW or IVF index are search targets", () => {
  assert.deepEqual(
    mediaTargets([IMAGENES, CLIENTES, CANCIONES]).map((target) => [target.key, target.methods]),
    [
      ["imagenes.ruta", ["HNSW", "IVF"]],
      ["canciones.ruta", ["IVF"]],
    ],
  );
});

test("a target knows its key, title and class columns", () => {
  assert.equal(IMAGES.keyColumn, "id");
  assert.equal(IMAGES.titleColumn, "titulo");
  assert.equal(IMAGES.categoryColumn, "categoria");
  assert.equal(SONGS.keyColumn, null);
  assert.equal(SONGS.categoryColumn, "genero");
});

test("the chosen target falls back to the first one", () => {
  assert.equal(chosenTarget([IMAGES, SONGS], "canciones.ruta"), SONGS);
  assert.equal(chosenTarget([IMAGES, SONGS], "otra.columna"), IMAGES);
  assert.equal(chosenTarget([], null), null);
});

test("two indexes are compared with each other, one index with two metrics", () => {
  assert.deepEqual(defaultSettings(["HNSW", "IVF"]), [
    { method: "HNSW", metric: "cosine", effort: "" },
    { method: "IVF", metric: "cosine", effort: "" },
  ]);
  assert.deepEqual(defaultSettings(["IVF"]), [
    { method: "IVF", metric: "cosine", effort: "" },
    { method: "IVF", metric: "euclidean", effort: "" },
  ]);
});

test("the sql is the similar_to query of the statement", () => {
  assert.equal(
    similaritySql(REQUEST),
    [
      "SELECT id, titulo, categoria, ruta, SIMILARITY_SCORE() AS score",
      "FROM imagenes",
      "WHERE SIMILAR_TO('data/datasets/abo/c8.jpg', k = 10)",
      "USING HNSW WITH METRIC = cosine",
      "ORDER BY score DESC;",
    ].join("\n"),
  );
});

test("the effort goes into the with clause under the option of the index", () => {
  const hnsw = similaritySql({ ...REQUEST, settings: { method: "HNSW", metric: "dot", effort: "120" } });
  const ivf = similaritySql({ ...REQUEST, settings: { method: "IVF", metric: "euclidean", effort: "8" } });

  assert.match(hnsw, /USING HNSW WITH METRIC = dot, ef_search = 120/);
  assert.match(ivf, /USING IVF WITH METRIC = euclidean, probes = 8/);
});

test("an effort that is not a positive integer is left to the index", () => {
  assert.equal(effortValue(""), null);
  assert.equal(effortValue("0"), null);
  assert.equal(effortValue("2.5"), null);
  assert.equal(effortValue("abc"), null);
  assert.equal(effortValue(" 40 "), 40);
});

test("quotes inside a path are doubled", () => {
  assert.equal(quoted("data/l'oreal.jpg"), "'data/l''oreal.jpg'");
});

test("the sample query lists the gallery columns", () => {
  assert.equal(sampleSql(SONGS, 12), "SELECT titulo, genero, ruta\nFROM canciones\nLIMIT 12;");
});

test("rows become gallery cards by column name", () => {
  const cards = mediaCards(
    response(
      ["id", "titulo", "categoria", "ruta", "score"],
      [
        [7, "Funda", "CELLULAR_PHONE_CASE", "a.jpg", 1],
        [9, null, "SHOES", "b.jpg", 0.5],
      ],
    ),
    IMAGES,
  );

  assert.deepEqual(cards, [
    { key: "7", path: "a.jpg", title: "Funda", category: "CELLULAR_PHONE_CASE", score: 1 },
    { key: "9", path: "b.jpg", title: null, category: "SHOES", score: 0.5 },
  ]);
});

test("a table without a key numbers its cards", () => {
  const cards = mediaCards(response(["titulo", "genero", "ruta"], [["Uno", "Rock", "x.mp3"]]), SONGS);

  assert.deepEqual(cards, [{ key: "0", path: "x.mp3", title: "Uno", category: "Rock", score: null }]);
});

test("a row becomes a query with its class", () => {
  assert.deepEqual(queryFromCard({ key: "1", path: "s/x.mp3", title: null, category: "Rock", score: null }), {
    path: "s/x.mp3",
    name: "x.mp3",
    kind: "audio",
    category: "Rock",
  });
  assert.equal(queryFromCard({ key: "1", path: "notas.csv", title: null, category: null, score: null }), null);
});

test("media kinds come from the extension", () => {
  assert.equal(mediaKindOf("a/B.JPG"), "image");
  assert.equal(mediaKindOf("a/b.flac"), "audio");
  assert.equal(mediaKindOf("a/b.csv"), null);
});

test("the score bar is relative to the best and never negative", () => {
  assert.equal(similarityShare(0.5, 1), 0.5);
  assert.equal(similarityShare(-0.2, 1), 0);
  assert.equal(similarityShare(2, 1), 1);
  assert.equal(similarityShare(null, 1), 0);
  assert.equal(similarityShare(1, 0), 0);
});

test("matches of the class are counted only when the class is known", () => {
  const cards = [
    { key: "1", path: "a.jpg", title: null, category: "SHOES", score: 1 },
    { key: "2", path: "b.jpg", title: null, category: "HOME", score: 0.5 },
  ];

  assert.equal(sameCategoryCount(cards, "SHOES"), 1);
  assert.equal(sameCategoryCount(cards, null), null);
});

test("a failure shared by both queries is shown once", () => {
  const failed = (error: string): PanelOutcome => ({
    sql: "",
    cards: [],
    plan: null,
    failure: { error, kind: "X", line: null, column: null, in_transaction: null },
    elapsedMs: null,
  });

  assert.equal(sharedFailure([failed("a"), failed("a")])?.error, "a");
  assert.equal(sharedFailure([failed("a"), failed("b")]), null);
  assert.equal(sharedFailure([failed("a")]), null);
});

test("k stays between one and the maximum", () => {
  assert.equal(clampK(0), 1);
  assert.equal(clampK(500), 100);
  assert.equal(clampK(7.4), 7);
  assert.equal(clampK(Number.NaN), 10);
});
