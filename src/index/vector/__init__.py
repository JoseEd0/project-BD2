"""Lo que comparten los índices de vectores: métricas, identificadores, búsqueda exacta."""

from .base import (
    DuplicateVectorIdError,
    VectorBatchResult,
    VectorHit,
    VectorIndex,
    VectorIndexFileError,
    VectorSearchResult,
    VectorSearchStats,
)
from .flat import FlatIndex
from .ids import record_id_of, vector_id_of
from .metrics import (
    InvalidVectorError,
    UnknownVectorMetricError,
    VectorMetric,
    vector_metric_named,
)

__all__ = [
    "DuplicateVectorIdError",
    "FlatIndex",
    "InvalidVectorError",
    "UnknownVectorMetricError",
    "VectorBatchResult",
    "VectorHit",
    "VectorIndex",
    "VectorIndexFileError",
    "VectorMetric",
    "VectorSearchResult",
    "VectorSearchStats",
    "record_id_of",
    "vector_id_of",
    "vector_metric_named",
]
