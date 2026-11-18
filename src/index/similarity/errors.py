"""Los errores de los índices de similitud."""

from __future__ import annotations

from errors import DomainError


class SimilarityIndexError(DomainError):
    """Lo que el índice de similitud no puede hacer con lo que se le pidió."""


class SimilarityEqualityError(SimilarityIndexError):
    """Se pidió una igualdad a un índice de similitud, que solo responde `SIMILAR_TO`."""


class MixedMediaError(SimilarityIndexError):
    """La columna mezcla imágenes y audios, o la consulta no es del tipo de la columna."""
