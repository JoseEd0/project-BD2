"""Lo que hizo una carga, paso por paso, con sus tiempos medidos."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TableLoad:
    """La carga de una tabla.

    Attributes:
        table: la tabla.
        rows: filas que tiene que tener al terminar.
        loaded: filas que envió esta carga (0 si ya estaba completa).
        seconds: desde la primera fila enviada hasta la respuesta del gestor, que llega
            después de construir los índices diferidos.
        deferred_indexes: índices que el gestor construyó de una vez al final.
    """

    table: str
    rows: int
    loaded: int
    seconds: float
    deferred_indexes: tuple[str, ...] = ()

    @property
    def skipped(self) -> bool:
        return self.loaded == 0


@dataclass(frozen=True, slots=True)
class IndexBuild:
    """Un `CREATE INDEX` de la carga; `created` es falso si el índice ya existía."""

    index: str
    created: bool
    seconds: float


@dataclass(frozen=True, slots=True)
class EmbeddingStep:
    """Los embeddings de CLIP: fotos distintas, cuántas se calcularon, cuántas ya estaban en
    la caché, y los segundos."""

    images: int
    computed: int
    cached: int
    seconds: float


@dataclass(frozen=True, slots=True)
class CopurchaseStep:
    """La materialización de las compras conjuntas: pares cargados, consultas `GROUP BY`
    (contando las que no cupieron en una respuesta) y segundos; `skipped` si ya estaba."""

    pairs: int
    queries: int
    seconds: float
    skipped: bool


@dataclass(slots=True)
class SeedReport:
    """Todos los pasos de una carga, en orden, y su duración total."""

    migrations_seconds: float = 0.0
    tables: list[TableLoad] = field(default_factory=list)
    indexes: list[IndexBuild] = field(default_factory=list)
    embeddings: EmbeddingStep | None = None
    copurchases: CopurchaseStep | None = None
    total_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
