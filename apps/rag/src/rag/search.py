"""Recuperación de fragmentos: BM25 del índice invertido, vecinos del HNSW y su fusión.

- `bm25`: `MATCH(content, ...) USING BM25` sobre el índice invertido en inglés. Encuentra
  las palabras exactas (siglas, nombres de sistemas) y no entiende otro idioma: a una
  pregunta en español se le quitan antes sus palabras vacías (`rag.language`), y le quedan
  los nombres y términos que comparte con los papers en inglés.
- `vector`: el embedding de la pregunta (`query: ...`) contra los de los fragmentos con
  `SIMILAR_TO` en el HNSW por coseno. Encuentra paráfrasis y responde a una pregunta en
  español con fragmentos en inglés.
- `hybrid`: las dos listas, `HYBRID_CANDIDATES` fragmentos cada una, fusionadas con RRF.

Los filtros por año y categoría (`SearchFilters`) son columnas de `rag_papers`, no de
`rag_chunks`, y cada método los lleva a su `WHERE` por el camino que el gestor resuelve
rápido (medido sobre los 98 585 fragmentos, ver `README.md`):

- BM25 pide primero los papers que cumplen el filtro y busca con `paper_id IN (...)`: el
  ranking es perezoso y el filtro descarta fragmentos a medida que salen, de 50 a 290 ms. Con
  un `JOIN` contra `rag_papers`, el ranking entrega todos los fragmentos con algún término
  antes de filtrar: 13,7 s.
- El vectorial va con un `JOIN` y las condiciones sobre `rag_papers`, entre los
  `FILTERED_VECTOR_CANDIDATES` vecinos más cercanos: de 320 a 450 ms. Con `paper_id IN
  (...)` sobre la misma tabla, el gestor prefiltra recorriendo todos los fragmentos: de 4 a
  27 s.

Todo va al gestor por HTTP con parámetros `$n`: el texto de la pregunta nunca se pega en el
SQL. Cada `Retriever` guarda el SQL de lo que corrió (`executed`), que `/search` devuelve.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Self

import anyio.to_thread
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from appkit import timed
from minigestor_client import AsyncClient, Record

from .embeddings import Embedder, Embeddings
from .fusion import RRF_K, reciprocal_rank_fusion
from .language import detect_language, without_stopwords
from .schema import AUTHOR_SEPARATOR, CHUNKS_TABLE, PAPERS_TABLE, SHORT_TEXT_LENGTH

DEFAULT_RESULTS = 10
MAX_RESULTS = 50
# Cuántos fragmentos aporta cada lista a la fusión. Con 50, un fragmento que una de las dos
# deja en el puesto 50 todavía suma 1/110 frente al 1/61 del primero.
HYBRID_CANDIDATES = 50
# Candidatos que el HNSW recorre por consulta. Más que los `HYBRID_CANDIDATES` que pide la
# búsqueda híbrida: con `ef_search` igual a k, los últimos vecinos salen sin margen y el
# recall de la cola cae.
HNSW_EF_SEARCH = 128
# Con un filtro, los vecinos que el HNSW entrega antes de que el `JOIN` descarte los de otros
# papers. Con 500, un filtro que deja el 6 % del corpus (el año 2025) todavía devuelve 50
# fragmentos; con 50 devolvía 12. El filtro más estrecho puede devolver menos de k.
FILTERED_VECTOR_CANDIDATES = 500
# Los años que un filtro puede pedir y cuántas categorías a la vez: el corpus es de
# 2025-2026 y tiene tres categorías; los topes solo acotan una petición abusiva.
MIN_FILTER_YEAR = 1900
MAX_FILTER_YEAR = 2100
MAX_FILTER_CATEGORIES = 16

EMBEDDING_STAGE = "embedding"
BM25_STAGE = "bm25"
VECTOR_STAGE = "vector"
PAPERS_STAGE = "papers"
FILTER_STAGE = "filter"

CHUNK_COLUMNS = "chunk_id, paper_id, page_start, page_end, content"
BM25_SQL = (
    f"SELECT {CHUNK_COLUMNS}, SCORE() AS score FROM {CHUNKS_TABLE} "
    "WHERE MATCH(content, $1) USING BM25 ORDER BY score DESC LIMIT $2"
)
VECTOR_SQL = (
    f"SELECT {CHUNK_COLUMNS}, SIMILARITY_SCORE() AS score FROM {CHUNKS_TABLE} "
    f"WHERE SIMILAR_TO(embedding, $1, k = $2) USING HNSW WITH ef_search = {HNSW_EF_SEARCH}"
)
CHUNK_ALIAS = "c"
PAPER_ALIAS = "p"
ALIASED_CHUNK_COLUMNS = ", ".join(
    f"{CHUNK_ALIAS}.{column} AS {column}" for column in CHUNK_COLUMNS.split(", ")
)
PAPER_COLUMNS = "paper_id, title, authors, year, url, file_path, media_type, license"
# Dentro de `MATCH`, AND, OR y NOT en mayúsculas y los paréntesis son operadores booleanos;
# una pregunta es texto libre, así que se pasa a minúsculas y sin paréntesis.
BOOLEAN_SYNTAX = re.compile(r"[()]")


class SearchMode(StrEnum):
    BM25 = "bm25"
    VECTOR = "vector"
    HYBRID = "hybrid"


Category = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SHORT_TEXT_LENGTH)
]
FilterYear = Annotated[int, Field(ge=MIN_FILTER_YEAR, le=MAX_FILTER_YEAR)]


class SearchFilters(BaseModel):
    """Los papers que pueden aportar fragmentos: publicados entre `year_from` y `year_to`
    (ambos incluidos; un documento sin año queda fuera si se pide alguno) y de alguna de las
    `categories` (la categoría primaria). Un campo vacío no filtra; uno desconocido es un
    error, para que un filtro mal escrito no busque en todo el corpus."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year_from: FilterYear | None = None
    year_to: FilterYear | None = None
    categories: tuple[Category, ...] = Field(default=(), max_length=MAX_FILTER_CATEGORIES)

    @model_validator(mode="after")
    def ordered_years(self) -> Self:
        if self.year_from is None or self.year_to is None:
            return self
        if self.year_from > self.year_to:
            raise ValueError("year_from no puede ser mayor que year_to")
        return self

    @property
    def active(self) -> bool:
        return self.year_from is not None or self.year_to is not None or bool(self.categories)

    def conditions(self, first_position: int, alias: str | None = None) -> tuple[str, list[object]]:
        """Las condiciones sobre `rag_papers` unidas con `AND`, con sus parámetros desde
        `$first_position`, y los valores de esos parámetros."""
        prefix = "" if alias is None else f"{alias}."
        parts: list[str] = []
        values: list[object] = []

        def placeholder(value: object) -> str:
            values.append(value)
            return f"${first_position + len(values) - 1}"

        if self.year_from is not None:
            parts.append(f"{prefix}year >= {placeholder(self.year_from)}")
        if self.year_to is not None:
            parts.append(f"{prefix}year <= {placeholder(self.year_to)}")
        if self.categories:
            listed = ", ".join(placeholder(category) for category in self.categories)
            parts.append(f"{prefix}primary_category IN ({listed})")
        return " AND ".join(parts), values


class ExecutedQuery(BaseModel):
    """Una consulta que la búsqueda corrió en el gestor: la etapa, su SQL con parámetros
    `$n` y cada parámetro como se muestra (JSON; un embedding, como `VECTOR(384)`)."""

    model_config = ConfigDict(frozen=True)

    stage: str
    sql: str
    parameters: list[str]


class Paper(BaseModel):
    """El documento de un fragmento, lo que necesita una cita."""

    model_config = ConfigDict(frozen=True)

    paper_id: str
    title: str
    authors: list[str]
    year: int | None
    url: str | None
    file_path: str
    media_type: str
    license: str | None


class ModeScore(BaseModel):
    """El puesto (desde 1) y el puntaje de un fragmento en la lista de un método: BM25, o
    el coseno de los embeddings."""

    model_config = ConfigDict(frozen=True)

    rank: int
    score: float


class SearchHit(BaseModel):
    """Un fragmento recuperado. `score` es el del modo pedido (BM25, coseno o RRF); `bm25`
    y `vector` dicen cómo lo vio cada método, o `None` si no lo trajo."""

    model_config = ConfigDict(frozen=True)

    rank: int
    chunk_id: str
    paper: Paper
    page_start: int
    page_end: int
    text: str
    score: float
    bm25: ModeScore | None
    vector: ModeScore | None


class Retriever:
    """Busca fragmentos en el gestor con el cliente y el modelo de embeddings del servicio;
    `rrf_k` es la constante de la fusión híbrida. Se crea uno por petición: `executed` junta
    el SQL de cada consulta que corrió."""

    def __init__(self, engine: AsyncClient, embedder: Embedder, rrf_k: int = RRF_K) -> None:
        self._engine = engine
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._executed: list[ExecutedQuery] = []

    @property
    def executed(self) -> list[ExecutedQuery]:
        return list(self._executed)

    async def search(
        self, query: str, mode: SearchMode, k: int, filters: SearchFilters | None = None
    ) -> list[SearchHit]:
        """Los `k` fragmentos más relevantes para `query` según `mode`, de mejor a peor, solo
        de los papers que cumplen `filters`."""
        scope = filters if filters is not None and filters.active else None
        allowed = await self._allowed_papers(scope) if mode is not SearchMode.VECTOR else None
        if mode is SearchMode.BM25:
            rows = await self._bm25(query, k, allowed)
            return await self._hits(rows, [(seen.score, seen, None) for seen in _ranked(rows)])
        if mode is SearchMode.VECTOR:
            rows = await self._vector(query, k, scope)
            return await self._hits(rows, [(seen.score, None, seen) for seen in _ranked(rows)])
        return await self._hybrid(query, k, scope, allowed)

    async def _hybrid(
        self,
        query: str,
        k: int,
        scope: SearchFilters | None,
        allowed: Sequence[str] | None,
    ) -> list[SearchHit]:
        depth = max(HYBRID_CANDIDATES, k)
        lexical, semantic = await asyncio.gather(
            self._bm25(query, depth, allowed), self._vector(query, depth, scope)
        )
        rows = {row["chunk_id"]: row for row in (*semantic, *lexical)}
        fused = reciprocal_rank_fusion(
            [[row["chunk_id"] for row in lexical], [row["chunk_id"] for row in semantic]],
            self._rrf_k,
        )[:k]
        scores = [
            (
                item.score,
                _mode_score(lexical, item.ranks[0]),
                _mode_score(semantic, item.ranks[1]),
            )
            for item in fused
        ]
        return await self._hits([rows[item.key] for item in fused], scores)

    async def _allowed_papers(self, scope: SearchFilters | None) -> list[str] | None:
        if scope is None:
            return None
        conditions, values = scope.conditions(1)
        sql = f"SELECT paper_id FROM {PAPERS_TABLE} WHERE {conditions}"
        rows = await self._fetch(FILTER_STAGE, sql, values)
        return [row["paper_id"] for row in rows]

    async def _bm25(self, query: str, limit: int, allowed: Sequence[str] | None) -> list[Record]:
        text = lexical_query(query)
        if allowed is None:
            return await self._fetch(BM25_STAGE, BM25_SQL, [text, limit])
        if not allowed:
            return []
        return await self._fetch(BM25_STAGE, bm25_within_sql(len(allowed)), [text, limit, *allowed])

    async def _vector(
        self, query: str, limit: int, scope: SearchFilters | None
    ) -> list[Record]:
        with timed(EMBEDDING_STAGE):
            vector: Embeddings = await anyio.to_thread.run_sync(self._embedder.embed_query, query)
        if scope is None:
            return await self._fetch(VECTOR_STAGE, VECTOR_SQL, [vector, limit])
        conditions, values = scope.conditions(4, PAPER_ALIAS)
        candidates = max(FILTERED_VECTOR_CANDIDATES, limit)
        return await self._fetch(
            VECTOR_STAGE, vector_within_sql(conditions), [vector, limit, candidates, *values]
        )

    async def _fetch(self, stage: str, sql: str, parameters: Sequence[object]) -> list[Record]:
        self._executed.append(
            ExecutedQuery(
                stage=stage, sql=sql, parameters=[_shown(value) for value in parameters]
            )
        )
        with timed(stage):
            return await self._engine.fetch_all(sql, list(parameters))

    async def _hits(
        self,
        rows: Sequence[Record],
        scores: Sequence[tuple[float, ModeScore | None, ModeScore | None]],
    ) -> list[SearchHit]:
        papers = await self._papers({row["paper_id"] for row in rows})
        return [
            SearchHit(
                rank=rank,
                chunk_id=row["chunk_id"],
                paper=papers[row["paper_id"]],
                page_start=row["page_start"],
                page_end=row["page_end"],
                text=row["content"],
                score=score,
                bm25=bm25,
                vector=vector,
            )
            for rank, (row, (score, bm25, vector)) in enumerate(zip(rows, scores, strict=True), 1)
        ]

    async def _papers(self, paper_ids: set[str]) -> dict[str, Paper]:
        if not paper_ids:
            return {}
        ordered = sorted(paper_ids)
        placeholders = ", ".join(f"${position}" for position in range(1, len(ordered) + 1))
        sql = f"SELECT {PAPER_COLUMNS} FROM {PAPERS_TABLE} WHERE paper_id IN ({placeholders})"
        rows = await self._fetch(PAPERS_STAGE, sql, ordered)
        return {row["paper_id"]: paper_of(row) for row in rows}


def lexical_query(question: str) -> str:
    """Lo que busca BM25 para `question`: sin las palabras vacías de su idioma si no es el
    del índice (el analizador inglés no quita «de», «la», «qué», y con ellas una pregunta en
    español solo encuentra los documentos del curso) y como texto libre para `MATCH`."""
    return plain_text_query(without_stopwords(question, detect_language(question)))


def bm25_within_sql(papers: int) -> str:
    """El BM25 limitado a `papers` documentos, que van en los parámetros desde `$3`."""
    listed = ", ".join(f"${position}" for position in range(3, papers + 3))
    return (
        f"SELECT {CHUNK_COLUMNS}, SCORE() AS score FROM {CHUNKS_TABLE} "
        f"WHERE MATCH(content, $1) AND paper_id IN ({listed}) "
        "USING BM25 ORDER BY score DESC LIMIT $2"
    )


def vector_within_sql(conditions: str) -> str:
    """Los vecinos del HNSW cuyo paper cumple `conditions` (sobre el alias `p`, con
    parámetros desde `$4`): `$3` candidatos, de los que quedan los `$2` mejores."""
    return (
        f"SELECT {ALIASED_CHUNK_COLUMNS}, SIMILARITY_SCORE() AS score "
        f"FROM {CHUNKS_TABLE} {CHUNK_ALIAS} JOIN {PAPERS_TABLE} {PAPER_ALIAS} "
        f"ON {CHUNK_ALIAS}.paper_id = {PAPER_ALIAS}.paper_id "
        f"WHERE SIMILAR_TO({CHUNK_ALIAS}.embedding, $1, k = $3) AND {conditions} "
        f"USING HNSW WITH ef_search = {FILTERED_VECTOR_CANDIDATES} "
        "ORDER BY score DESC LIMIT $2"
    )


def plain_text_query(question: str) -> str:
    """`question` como texto libre para `MATCH`, sin sintaxis booleana."""
    return BOOLEAN_SYNTAX.sub(" ", question).casefold()


def _shown(value: object) -> str:
    if isinstance(value, np.ndarray):
        return f"VECTOR({value.size})"
    return json.dumps(value, ensure_ascii=False)


def _ranked(rows: Sequence[Record]) -> list[ModeScore]:
    return [ModeScore(rank=rank, score=row["score"]) for rank, row in enumerate(rows, start=1)]


def _mode_score(rows: Sequence[Record], rank: int | None) -> ModeScore | None:
    if rank is None:
        return None
    return ModeScore(rank=rank, score=rows[rank - 1]["score"])


def paper_of(row: Record) -> Paper:
    """El `Paper` de una fila de `rag_papers` con `PAPER_COLUMNS`."""
    authors = row["authors"] or ""
    return Paper.model_validate(
        {**row, "authors": authors.split(AUTHOR_SEPARATOR) if authors else []}
    )
