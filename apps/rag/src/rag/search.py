"""Recuperación de fragmentos: BM25 del índice invertido, vecinos del HNSW y su fusión.

- `bm25`: `MATCH(content, ...) USING BM25` sobre el índice invertido en inglés. Encuentra
  las palabras exactas (siglas, nombres de sistemas) y no entiende otro idioma: a una
  pregunta en español se le quitan antes sus palabras vacías (`rag.language`), y le quedan
  los nombres y términos que comparte con los papers en inglés.
- `vector`: el embedding de la pregunta (`query: ...`) contra los de los fragmentos con
  `SIMILAR_TO` en el HNSW por coseno. Encuentra paráfrasis y responde a una pregunta en
  español con fragmentos en inglés.
- `hybrid`: las dos listas, `HYBRID_CANDIDATES` fragmentos cada una, fusionadas con RRF.

Todo va al gestor por HTTP con parámetros `$n`: el texto de la pregunta nunca se pega en el
SQL.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from enum import StrEnum

import anyio.to_thread
from pydantic import BaseModel, ConfigDict

from appkit import timed
from minigestor_client import AsyncClient, Record

from .embeddings import Embedder, Embeddings
from .fusion import RRF_K, reciprocal_rank_fusion
from .language import detect_language, without_stopwords
from .schema import AUTHOR_SEPARATOR, CHUNKS_TABLE, PAPERS_TABLE

DEFAULT_RESULTS = 10
MAX_RESULTS = 50
# Cuántos fragmentos aporta cada lista a la fusión. Con 50, un fragmento que una de las dos
# deja en el puesto 50 todavía suma 1/110 frente al 1/61 del primero.
HYBRID_CANDIDATES = 50
# Candidatos que el HNSW recorre por consulta. Más que los `HYBRID_CANDIDATES` que pide la
# búsqueda híbrida: con `ef_search` igual a k, los últimos vecinos salen sin margen y el
# recall de la cola cae.
HNSW_EF_SEARCH = 128

EMBEDDING_STAGE = "embedding"
BM25_STAGE = "bm25"
VECTOR_STAGE = "vector"
PAPERS_STAGE = "papers"

CHUNK_COLUMNS = "chunk_id, paper_id, page_start, page_end, content"
BM25_SQL = (
    f"SELECT {CHUNK_COLUMNS}, SCORE() AS score FROM {CHUNKS_TABLE} "
    "WHERE MATCH(content, $1) USING BM25 ORDER BY score DESC LIMIT $2"
)
VECTOR_SQL = (
    f"SELECT {CHUNK_COLUMNS}, SIMILARITY_SCORE() AS score FROM {CHUNKS_TABLE} "
    f"WHERE SIMILAR_TO(embedding, $1, k = $2) USING HNSW WITH ef_search = {HNSW_EF_SEARCH}"
)
PAPER_COLUMNS = "paper_id, title, authors, year, url, file_path, media_type, license"
# Dentro de `MATCH`, AND, OR y NOT en mayúsculas y los paréntesis son operadores booleanos;
# una pregunta es texto libre, así que se pasa a minúsculas y sin paréntesis.
BOOLEAN_SYNTAX = re.compile(r"[()]")


class SearchMode(StrEnum):
    BM25 = "bm25"
    VECTOR = "vector"
    HYBRID = "hybrid"


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
    `rrf_k` es la constante de la fusión híbrida."""

    def __init__(self, engine: AsyncClient, embedder: Embedder, rrf_k: int = RRF_K) -> None:
        self._engine = engine
        self._embedder = embedder
        self._rrf_k = rrf_k

    async def search(self, query: str, mode: SearchMode, k: int) -> list[SearchHit]:
        """Los `k` fragmentos más relevantes para `query` según `mode`, de mejor a peor."""
        if mode is SearchMode.BM25:
            rows = await self._bm25(query, k)
            return await self._hits(rows, [(seen.score, seen, None) for seen in _ranked(rows)])
        if mode is SearchMode.VECTOR:
            rows = await self._vector(query, k)
            return await self._hits(rows, [(seen.score, None, seen) for seen in _ranked(rows)])
        return await self._hybrid(query, k)

    async def _hybrid(self, query: str, k: int) -> list[SearchHit]:
        depth = max(HYBRID_CANDIDATES, k)
        lexical, semantic = await asyncio.gather(
            self._bm25(query, depth), self._vector(query, depth)
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

    async def _bm25(self, query: str, limit: int) -> list[Record]:
        with timed(BM25_STAGE):
            return await self._engine.fetch_all(BM25_SQL, [lexical_query(query), limit])

    async def _vector(self, query: str, limit: int) -> list[Record]:
        with timed(EMBEDDING_STAGE):
            vector: Embeddings = await anyio.to_thread.run_sync(self._embedder.embed_query, query)
        with timed(VECTOR_STAGE):
            return await self._engine.fetch_all(VECTOR_SQL, [vector, limit])

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
        with timed(PAPERS_STAGE):
            rows = await self._engine.fetch_all(sql, ordered)
        return {row["paper_id"]: paper_of(row) for row in rows}


def lexical_query(question: str) -> str:
    """Lo que busca BM25 para `question`: sin las palabras vacías de su idioma si no es el
    del índice (el analizador inglés no quita «de», «la», «qué», y con ellas una pregunta en
    español solo encuentra los documentos del curso) y como texto libre para `MATCH`."""
    return plain_text_query(without_stopwords(question, detect_language(question)))


def plain_text_query(question: str) -> str:
    """`question` como texto libre para `MATCH`, sin sintaxis booleana."""
    return BOOLEAN_SYNTAX.sub(" ", question).casefold()


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
