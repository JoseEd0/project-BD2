"""Lo que comparten los índices de vectores: métricas, identificadores, búsqueda exacta."""

from .ids import record_id_of, vector_id_of
from .metrics import (
    InvalidVectorError,
    UnknownVectorMetricError,
    VectorMetric,
    vector_metric_named,
)

__all__ = [
    "InvalidVectorError",
    "UnknownVectorMetricError",
    "VectorMetric",
    "record_id_of",
    "vector_id_of",
    "vector_metric_named",
]
