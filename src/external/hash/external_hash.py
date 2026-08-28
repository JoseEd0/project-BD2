"""Hashing externo: agrupar y juntar sin que todo quepa en memoria.

La idea es siempre la misma: **partir por hash**. Si dos filas tienen la misma clave, su
hash es el mismo, así que caen en la misma partición. Eso convierte un problema grande en
`P` problemas independientes y pequeños, cada uno de los cuales sí cabe en memoria.

    entrada  ──► hash(clave) mod P ──► partición 0, 1, …, P-1 (archivos en disco)
                                            │
                                            └─► se procesa una partición a la vez

Ver `README.md` para el análisis de coste y sus límites.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from types import TracebackType

from config import EngineConfig
from external.runs import RunReader, RunWriter
from hashing import canonical_key_bytes, stable_hash
from index.keys import Key

GROUP_PREFIX = "group"
LEFT_PREFIX = "left"
RIGHT_PREFIX = "right"
PARTITION_SUFFIX = ".part"


class HashPartitioner:
    """Reparte registros en `P` archivos según el hash de su clave."""

    def __init__(
        self,
        directory: Path,
        prefix: str,
        record_size: int,
        partition_count: int,
        key_of: Callable[[bytes], Key],
        config: EngineConfig,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._directory = directory
        self._prefix = prefix
        self._record_size = record_size
        self._partition_count = partition_count
        self._key_of = key_of
        self._config = config
        self._paths = [
            directory / f"{prefix}{index}{PARTITION_SUFFIX}" for index in range(partition_count)
        ]
        self.partition_sizes = [0] * partition_count

    @property
    def partition_count(self) -> int:
        return self._partition_count

    def write(self, records: Iterable[bytes]) -> None:
        """Vuelca cada registro en la partición que le toca."""
        writers = [
            RunWriter(path, self._record_size, self._config) for path in self._paths
        ]
        try:
            for record in records:
                index = self.partition_of(self._key_of(record))
                writers[index].append(record)
                self.partition_sizes[index] += 1
        finally:
            for writer in writers:
                writer.close()

    def read(self, index: int) -> Iterator[bytes]:
        """Recorre una partición trayendo una página cada vez."""
        with RunReader(self._paths[index], self._record_size, self._config) as reader:
            yield from reader

    def partition_of(self, key: Key) -> int:
        return stable_hash(canonical_key_bytes(key)) % self._partition_count

    def close(self) -> None:
        for path in self._paths:
            path.unlink(missing_ok=True)


class ExternalHashGrouper:
    """Agrupa registros por clave usando disco para las particiones.

    Complejidad, con `N` registros y `P` particiones:

    * E/S: `2N` (una escritura y una lectura por registro);
    * memoria: la partición más grande, en torno a `N/P` registros.

    A diferencia de agrupar por ordenamiento externo, **no ordena**: los grupos salen en el
    orden en que aparecen dentro de cada partición. Quien necesite orden lo pide aparte.
    """

    def __init__(
        self,
        directory: Path,
        record_size: int,
        key_of: Callable[[bytes], Key],
        config: EngineConfig,
    ) -> None:
        self._partitioner = HashPartitioner(
            directory, GROUP_PREFIX, record_size, config.hash_partitions, key_of, config
        )
        self._key_of = key_of

    @property
    def partition_sizes(self) -> list[int]:
        return self._partitioner.partition_sizes

    def group(self, records: Iterable[bytes]) -> Iterator[tuple[Key, list[bytes]]]:
        """Pares `(clave, registros de esa clave)`."""
        self._partitioner.write(records)
        for index in range(self._partitioner.partition_count):
            groups: dict[Key, list[bytes]] = {}
            for record in self._partitioner.read(index):
                groups.setdefault(self._key_of(record), []).append(record)
            yield from groups.items()

    def close(self) -> None:
        self._partitioner.close()

    def __enter__(self) -> ExternalHashGrouper:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class ExternalHashJoin:
    """Junta dos entradas por igualdad de clave (*grace hash join*).

    Se particionan **las dos** entradas con el mismo hash. Como las claves iguales caen en
    la misma partición, basta con cargar en memoria la partición izquierda y recorrer la
    derecha comparando.

    Complejidad, con `N` y `M` registros:

    * E/S: `2(N + M)`;
    * memoria: la partición izquierda más grande, en torno a `N/P`;
    * comparaciones: `O(N + M)` en vez de las `O(N · M)` del bucle anidado.
    """

    def __init__(
        self,
        directory: Path,
        left_record_size: int,
        right_record_size: int,
        left_key_of: Callable[[bytes], Key],
        right_key_of: Callable[[bytes], Key],
        config: EngineConfig,
    ) -> None:
        partitions = config.hash_partitions
        self._left = HashPartitioner(
            directory, LEFT_PREFIX, left_record_size, partitions, left_key_of, config
        )
        self._right = HashPartitioner(
            directory, RIGHT_PREFIX, right_record_size, partitions, right_key_of, config
        )
        self._left_key_of = left_key_of
        self._right_key_of = right_key_of

    @property
    def partition_count(self) -> int:
        return self._left.partition_count

    def join(
        self, left_records: Iterable[bytes], right_records: Iterable[bytes]
    ) -> Iterator[tuple[bytes, bytes]]:
        """Pares `(fila izquierda, fila derecha)` con la misma clave."""
        self._left.write(left_records)
        self._right.write(right_records)
        for index in range(self.partition_count):
            table: dict[Key, list[bytes]] = {}
            for record in self._left.read(index):
                table.setdefault(self._left_key_of(record), []).append(record)
            if not table:
                continue
            for probe in self._right.read(index):
                for match in table.get(self._right_key_of(probe), ()):
                    yield match, probe

    def close(self) -> None:
        self._left.close()
        self._right.close()

    def __enter__(self) -> ExternalHashJoin:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
