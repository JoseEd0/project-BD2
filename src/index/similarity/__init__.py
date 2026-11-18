"""Índices de similitud (HNSW e IVF) sobre una columna con rutas de imágenes o audios."""

from .base import SimilarityIndex, SimilarityQuery, SimilarityResult, SimilarRow
from .errors import MixedMediaError, SimilarityEqualityError, SimilarityIndexError
from .media import MediaResolver, MediaSimilarityIndex
from .vectors import HNSW_KIND, IVF_KIND, VectorIndexSettings

__all__ = [
    "HNSW_KIND",
    "IVF_KIND",
    "MediaResolver",
    "MediaSimilarityIndex",
    "MixedMediaError",
    "SimilarRow",
    "SimilarityEqualityError",
    "SimilarityIndex",
    "SimilarityIndexError",
    "SimilarityQuery",
    "SimilarityResult",
    "VectorIndexSettings",
]
