"""Reunión por bucles anidados en bloques (*block nested loop join*).

Cuando la condición de un JOIN no contiene ninguna igualdad —`ON a.precio < b.tope`— no
hay clave por la que repartir las filas, y no queda más que comparar cada fila de un lado
con cada fila del otro. Lo que sí se puede acotar es la memoria y el número de lecturas:

    derecha  ──► archivo temporal (se escribe una vez)
    izquierda ──► bloques de B filas en memoria
                      │
                      └─► por cada bloque, una pasada por el archivo de la derecha

Ver `README.md` para el coste.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from itertools import islice
from pathlib import Path
from types import TracebackType

from config import EngineConfig
from external.joining import Pair, PairTest, Unmatched
from external.runs import RunReader, RunWriter, buffered_records, remove_if_empty

INNER_FILE = "inner.run"


class BlockNestedLoopJoin:
    """Junta dos entradas comparando cada par de filas con una condición cualquiera.

    Complejidad, con `N` filas a la izquierda, `M` a la derecha y bloques de `B` filas:

    * comparaciones: `N · M`, inevitables sin una igualdad que las reparta;
    * E/S: `M` escrituras y `⌈N/B⌉ · M` lecturas, en vez de las `N · M` del bucle ingenuo;
    * memoria: un bloque de la izquierda (`sort_buffer_pages` páginas), una página de la
      derecha y, si se conservan las filas derechas sin pareja, un byte por cada una.
    """

    def __init__(
        self,
        directory: Path,
        left_record_size: int,
        right_record_size: int,
        accepts: PairTest,
        config: EngineConfig,
        unmatched: Unmatched = Unmatched.NONE,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._directory = directory
        self._inner_path = directory / INNER_FILE
        self._right_record_size = right_record_size
        self._accepts = accepts
        self._config = config
        self._unmatched = unmatched
        self._block_rows = buffered_records(config, left_record_size)

    def join(self, left_records: Iterable[bytes], right_records: Iterable[bytes]) -> Iterator[Pair]:
        """Pares que cumplen la condición, y las filas sin pareja que la reunión conserva."""
        right_paired = bytearray(self._store_inner(right_records))
        lefts = iter(left_records)
        while block := list(islice(lefts, self._block_rows)):
            left_paired = bytearray(len(block))
            for number, right in enumerate(self._inner()):
                for position, left in enumerate(block):
                    if self._accepts(left, right):
                        left_paired[position] = right_paired[number] = True
                        yield left, right
            if Unmatched.LEFT in self._unmatched:
                yield from (
                    (left, None)
                    for left, paired in zip(block, left_paired, strict=True)
                    if not paired
                )
        if Unmatched.RIGHT in self._unmatched:
            yield from (
                (None, right)
                for right, paired in zip(self._inner(), right_paired, strict=True)
                if not paired
            )

    def close(self) -> None:
        self._inner_path.unlink(missing_ok=True)
        remove_if_empty(self._directory)

    def __enter__(self) -> BlockNestedLoopJoin:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _store_inner(self, records: Iterable[bytes]) -> int:
        """Vuelca la entrada derecha a disco, para poder recorrerla una vez por bloque."""
        with RunWriter(self._inner_path, self._right_record_size, self._config) as writer:
            writer.extend(records)
            return writer.record_count

    def _inner(self) -> Iterator[bytes]:
        with RunReader(self._inner_path, self._right_record_size, self._config) as reader:
            yield from reader
