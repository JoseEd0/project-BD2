"""Un corpus diminuto con la forma del de `rag.corpus` y un modelo de embeddings de mentira.

Cuatro documentos y siete fragmentos en inglés sobre temas que no se pisan, para que cada
pregunta tenga un fragmento correcto. El modelo de mentira da a cada texto un vector al azar
fijado por su huella: sirve para probar la ingesta y la forma de las respuestas sin el modelo
real; lo que depende del significado se prueba con el real.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from rag.embeddings import EMBEDDING_DIMENSIONS, Embeddings, PassageEmbeddings, l2_normalized
from rag.files import sha256_of_text, write_jsonl
from rag.pipeline import CorpusPaths
from rag.records import ChunkRecord, Paper

BTREE_CHUNK = "2501.00001#0"
BM25_CHUNK = "2501.00002#0"
HNSW_CHUNK = "2501.00003#0"
RTREE_CHUNK = "2501.00003#1"
COURSE_PAPER = "curso:informe"

DOCUMENTS = {
    "2501.00001": (
        "B+ Trees for Range Queries",
        (
            "A B+ tree keeps every row in its leaves and links the leaves left to right, so a "
            "range query descends once to the first key and then scans the linked leaves.",
            "When a leaf overflows the node is split in two halves and the middle key is copied "
            "into the parent; a split can propagate up to the root, which grows the tree.",
        ),
    ),
    "2501.00002": (
        "Ranking Documents with BM25",
        (
            "BM25 ranks documents by term frequency saturated with the parameter k1 and "
            "normalized by document length with the parameter b, times the inverse document "
            "frequency of each query term.",
            "An inverted index maps every term to its posting list, the sorted identifiers of "
            "the documents that contain it, so a query only reads the lists of its terms.",
        ),
    ),
    "2501.00003": (
        "Graph Indexes for Nearest Neighbor Search",
        (
            "HNSW builds a hierarchy of proximity graphs; a search starts at the top layer and "
            "greedily walks towards the query vector before descending to denser layers.",
            "The R-tree groups nearby points into minimum bounding rectangles and prunes every "
            "rectangle that cannot contain an answer to a spatial query.",
        ),
    ),
    COURSE_PAPER: (
        "Informe del proyecto",
        (
            "El informe mide el archivo secuencial contra el heap file y el hash extensible con "
            "inserciones, búsquedas y borrados por clave.",
        ),
    ),
}


def paper_record(paper_id: str, title: str, chunks: int) -> Paper:
    course = paper_id == COURSE_PAPER
    return Paper(
        paper_id=paper_id,
        source="curso" if course else "arxiv",
        title=title,
        authors=() if course else ("Ada Lovelace", "Edgar F. Codd"),
        year=None if course else 2025,
        abstract="",
        primary_category="curso" if course else "cs.DB",
        categories=(),
        license=None if course else "http://creativecommons.org/licenses/by/4.0/",
        url=None if course else f"https://arxiv.org/abs/{paper_id}",
        file_path=f"pdf/{paper_id.replace(':', '_')}.pdf",
        media_type="application/pdf",
        sha256=sha256_of_text(paper_id),
        bytes=1,
        pages=chunks + 1,
        empty_pages=0,
        words=1,
        chunks=chunks,
    )


def chunk_record(paper_id: str, ordinal: int, text: str) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=f"{paper_id}#{ordinal}",
        paper_id=paper_id,
        ordinal=ordinal,
        page_start=ordinal + 1,
        page_end=ordinal + 2,
        words=len(text.split()),
        text=text,
        sha256=sha256_of_text(text),
    )


def write_corpus(root: Path) -> CorpusPaths:
    corpus = CorpusPaths(root)
    papers = [paper_record(paper, title, len(texts)) for paper, (title, texts) in DOCUMENTS.items()]
    chunks = [
        chunk_record(paper, ordinal, text)
        for paper, (_, texts) in DOCUMENTS.items()
        for ordinal, text in enumerate(texts)
    ]
    write_jsonl(corpus.papers_file, (paper.to_json() for paper in papers))
    write_jsonl(corpus.chunks_file, (chunk.to_json() for chunk in chunks))
    return corpus


def chunk_count() -> int:
    return sum(len(texts) for _, texts in DOCUMENTS.values())


class HashEmbedder:
    """Un vector al azar por texto, siempre el mismo para el mismo texto; cuenta lo que calcula."""

    model_id = "hash-embedder"

    def __init__(self) -> None:
        self.passages_embedded = 0

    def embed_query(self, question: str) -> Embeddings:
        return self._vectors([question])[0]

    def embed_passages(self, passages: Sequence[str]) -> PassageEmbeddings:
        self.passages_embedded += len(passages)
        return PassageEmbeddings(self._vectors(passages), truncated=0)

    def _vectors(self, texts: Sequence[str]) -> Embeddings:
        rows = [
            np.random.default_rng(int(hashlib.sha256(text.encode()).hexdigest(), 16))
            .standard_normal(EMBEDDING_DIMENSIONS)
            .astype(np.float32)
            for text in texts
        ]
        return l2_normalized(np.vstack(rows))
