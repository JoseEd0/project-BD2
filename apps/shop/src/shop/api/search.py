"""La búsqueda por palabras que perdona erratas: «guitr» busca «guitar» y lo dice.

Cada palabra que no está en el vocabulario de las fichas (o que tiene una vecina mucho más
frecuente) se cambia por la más cercana (`shop.spelling`) y se busca la consulta corregida
con el mismo BM25 de `/busqueda/texto`. Si la corregida no encuentra nada se busca la
original; `exacta` la busca tal cual. El vocabulario se lee una vez por proceso de
`products.csv`, el archivo que cargó el gestor.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from appkit import Engine
from shop.catalog.rows import PRODUCTS_CSV
from shop.settings import ShopSettings
from shop.spelling import Vocabulary, load_vocabulary

from .filters import Filter
from .models import TextSearchResult
from .routes import SearchText, Settings, bm25_search, search_limit

router = APIRouter()


class RememberedVocabulary:
    """El vocabulario de las fichas, leído la primera vez que se necesita."""

    def __init__(self, settings: ShopSettings) -> None:
        self._settings = settings
        self._vocabulary: Vocabulary | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> Vocabulary:
        async with self._lock:
            if self._vocabulary is None:
                self._vocabulary = await run_in_threadpool(self._load)
            return self._vocabulary

    def _load(self) -> Vocabulary:
        root = self._settings.photos_root
        return Vocabulary.from_texts([]) if root is None else load_vocabulary(root / PRODUCTS_CSV)


def vocabulary_of(request: Request) -> RememberedVocabulary:
    vocabulary: RememberedVocabulary = request.app.state.vocabulary
    return vocabulary


VocabularyOf = Annotated[RememberedVocabulary, Depends(vocabulary_of)]


@router.get("/busqueda/productos")
async def forgiving_search(
    q: SearchText,
    engine: Engine,
    settings: Settings,
    filters: Filter,
    vocabulary: VocabularyOf,
    limite: Annotated[int | None, Query(ge=1)] = None,
    exacta: bool = False,
) -> TextSearchResult:
    """BM25 sobre la consulta corregida, con `corregida` si se cambió alguna palabra; con
    `exacta`, sobre `q` tal cual."""
    limit = search_limit(limite, settings)
    correction = None if exacta else (await vocabulary.get()).correct(q)
    if correction is not None:
        corrected = await bm25_search(engine, correction.query, limit, filters)
        if corrected:
            return TextSearchResult(consulta=q, corregida=correction.query, resultados=corrected)
    found = await bm25_search(engine, q, limit, filters)
    return TextSearchResult(consulta=q, corregida=None, resultados=found)
