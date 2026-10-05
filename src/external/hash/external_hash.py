"""Hashing externo: agrupar y juntar sin que todo quepa en memoria.

La idea es siempre la misma: **partir por hash**. Si dos filas tienen la misma clave, su
hash es el mismo, así que caen en la misma partición. Eso convierte un problema grande en
`P` problemas independientes y pequeños, cada uno de los cuales sí cabe en memoria.

    entrada  ──► hash(clave) mod P ──► partición 0, 1, …, P-1 (archivos en disco)
                                            │
                                            └─► se procesa una partición a la vez

Si una partición sigue sin caber, se vuelve a partir con **otra** función de hash, que
separa las claves que con la primera coincidían. Ver `README.md` para el análisis de coste.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from types import TracebackType
from typing import TypeVar

from config import EngineConfig
from external.joining import Pair, PairTest, Unmatched, any_pair
from external.loop import BlockNestedLoopJoin
from external.runs import RunReader, RunWriter, buffered_records, remove_if_empty
from hashing import canonical_key_bytes, stable_hash
from index.keys import Key

GROUP_PREFIX = "group"
LEFT_PREFIX = "left"
RIGHT_PREFIX = "right"
BLOCKS_DIRECTORY = "blocks"
PARTITION_SUFFIX = ".part"
SALT_BYTES = 2

State = TypeVar("State")


class HashPartitioner:
    """Reparte registros en `P` archivos según el hash de su clave.

    Cada `level` usa una función de hash distinta: al volver a repartir una partición que
    salió demasiado grande, las claves que antes coincidían se separan.
    """

    def __init__(
        self,
        directory: Path,
        prefix: str,
        record_size: int,
        key_of: Callable[[bytes], Key],
        config: EngineConfig,
        level: int = 0,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._directory = directory
        self._record_size = record_size
        self._key_of = key_of
        self._config = config
        self._salt = level.to_bytes(SALT_BYTES, "little")
        self._paths = [
            directory / f"{prefix}-{level}-{index}{PARTITION_SUFFIX}"
            for index in range(config.hash_partitions)
        ]
        self.partition_sizes = [0] * config.hash_partitions

    @property
    def partition_count(self) -> int:
        return len(self._paths)

    @property
    def record_count(self) -> int:
        return sum(self.partition_sizes)

    def write(self, records: Iterable[bytes]) -> None:
        """Vuelca cada registro en la partición que le toca."""
        writers = [RunWriter(path, self._record_size, self._config) for path in self._paths]
        try:
            for record in records:
                index = self.partition_of(self._key_of(record))
                writers[index].append(record)
                self.partition_sizes[index] += 1
        finally:
            for writer in writers:
                writer.close()

    def read(self, index: int) -> Iterator[bytes]:
        """Recorre una partición trayendo una página cada vez, en el orden en que se escribió."""
        with RunReader(self._paths[index], self._record_size, self._config) as reader:
            yield from reader

    def partition_of(self, key: Key) -> int:
        return stable_hash(canonical_key_bytes(key), self._salt) % self.partition_count

    def close(self) -> None:
        for path in self._paths:
            path.unlink(missing_ok=True)
        remove_if_empty(self._directory)


class ExternalHashGrouper:
    """Agrupa registros por clave y reduce cada grupo a un estado, usando disco.

    De un grupo no se guardan sus registros, sino lo que `step` va acumulando al verlos
    pasar: un contador, una suma, el primero que llegó. Una clave con un millón de filas
    ocupa lo mismo que una con dos.

    Lo que sí tiene que caber en memoria son los estados de una partición. Si una trae más
    grupos de los que caben en el buffer, se vuelve a repartir con otra función de hash,
    las veces que haga falta: cada nivel divide sus grupos entre `P`.

    Complejidad, con `N` registros, `G` grupos, `P` particiones y `B` grupos en el buffer:

    * E/S: `2N` si `G ≤ P · B`; en general `2N` por nivel, con `⌈log_P(G/B)⌉` niveles;
    * memoria: como mucho `B` estados.

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
        self._directory = directory
        self._record_size = record_size
        self._key_of = key_of
        self._config = config
        self._memory_groups = buffered_records(config, record_size)
        self._open: list[HashPartitioner] = []
        self.partition_sizes: list[int] = []
        self.levels = 0

    def reduce(
        self,
        records: Iterable[bytes],
        start: Callable[[], State],
        step: Callable[[State, bytes], State],
    ) -> Iterator[tuple[Key, State]]:
        """Pares `(clave, estado)`.

        El estado de un grupo es `step` aplicado a cada uno de sus registros, en el orden
        en que llegaron, partiendo de `start()`.
        """
        return self._reduce_level(records, start, step, 0)

    def close(self) -> None:
        while self._open:
            self._open.pop().close()

    def __enter__(self) -> ExternalHashGrouper:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _reduce_level(
        self,
        records: Iterable[bytes],
        start: Callable[[], State],
        step: Callable[[State, bytes], State],
        level: int,
    ) -> Iterator[tuple[Key, State]]:
        partitioner = self._partition(records, level)
        for index in range(partitioner.partition_count):
            states = self._states_of(partitioner.read(index), start, step)
            if states is None:
                yield from self._reduce_level(partitioner.read(index), start, step, level + 1)
            else:
                yield from states.items()
        self._open.remove(partitioner)
        partitioner.close()

    def _partition(self, records: Iterable[bytes], level: int) -> HashPartitioner:
        partitioner = HashPartitioner(
            self._directory, GROUP_PREFIX, self._record_size, self._key_of, self._config, level
        )
        self._open.append(partitioner)
        partitioner.write(records)
        if level == 0:
            self.partition_sizes = partitioner.partition_sizes
        self.levels = max(self.levels, level + 1)
        return partitioner

    def _states_of(
        self,
        records: Iterable[bytes],
        start: Callable[[], State],
        step: Callable[[State, bytes], State],
    ) -> dict[Key, State] | None:
        """Estado de cada grupo de una partición, o `None` si son más de los que caben."""
        states: dict[Key, State] = {}
        for record in records:
            key = self._key_of(record)
            if key not in states:
                if len(states) == self._memory_groups:
                    return None
                states[key] = start()
            states[key] = step(states[key], record)
        return states


class ExternalHashJoin:
    """Junta dos entradas por igualdad de clave (*grace hash join*).

    Se particionan **las dos** entradas con el mismo hash. Como las claves iguales caen en
    la misma partición, basta con cargar en memoria la partición izquierda y recorrer la
    derecha comparando.

    Una partición izquierda que no cabe en el buffer se vuelve a repartir, junto con su
    pareja derecha, con otra función de hash. Si la nueva función tampoco separa nada es
    que sus filas comparten clave, y no hay hash que las reparta: esa partición se resuelve
    por bucles anidados en bloques, que no necesita tenerla entera en memoria.

    Una clave con NULL no es igual a ninguna, ni a otro NULL: esas filas nunca emparejan.
    `accepts` añade condiciones a la igualdad de claves, y `unmatched` dice qué filas
    sin pareja se entregan de todos modos, que es lo que distingue un `LEFT`, un `RIGHT`
    o un `FULL JOIN` de uno interno.

    Complejidad, con `N` y `M` registros y `B` registros en el buffer:

    * E/S: `2(N + M)` si las particiones caben, y otro tanto por cada nivel extra;
    * memoria: como mucho `B` registros de la izquierda;
    * comparaciones: `O(N + M)` en vez de las `O(N · M)` del bucle anidado, salvo entre
      las filas de una misma clave muy repetida.
    """

    def __init__(
        self,
        directory: Path,
        left_record_size: int,
        right_record_size: int,
        left_key_of: Callable[[bytes], Key],
        right_key_of: Callable[[bytes], Key],
        config: EngineConfig,
        accepts: PairTest = any_pair,
        unmatched: Unmatched = Unmatched.NONE,
    ) -> None:
        self._directory = directory
        self._left_record_size = left_record_size
        self._right_record_size = right_record_size
        self._left_key_of = left_key_of
        self._right_key_of = right_key_of
        self._config = config
        self._accepts = accepts
        self._unmatched = unmatched
        self._memory_records = buffered_records(config, left_record_size)
        self._open: list[HashPartitioner] = []
        self.levels = 0
        self.block_joins = 0

    def join(self, left_records: Iterable[bytes], right_records: Iterable[bytes]) -> Iterator[Pair]:
        """Pares `(fila izquierda, fila derecha)` con la misma clave, y las filas sin pareja
        que la reunión conserva, con `None` en el otro lado."""
        return self._join_level(left_records, right_records, 0)

    def close(self) -> None:
        while self._open:
            self._open.pop().close()

    def __enter__(self) -> ExternalHashJoin:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _join_level(
        self, left_records: Iterable[bytes], right_records: Iterable[bytes], level: int
    ) -> Iterator[Pair]:
        left = self._partition(
            left_records, LEFT_PREFIX, self._left_record_size, self._left_key_of, level
        )
        right = self._partition(
            right_records, RIGHT_PREFIX, self._right_record_size, self._right_key_of, level
        )
        self.levels = max(self.levels, level + 1)
        for index, size in enumerate(left.partition_sizes):
            lefts, rights = left.read(index), right.read(index)
            if size <= self._memory_records:
                yield from self._join_in_memory(lefts, rights)
            elif level > 0 and size == left.record_count:
                yield from self._join_by_blocks(lefts, rights, level)
            else:
                yield from self._join_level(lefts, rights, level + 1)
        for partitioner in (right, left):
            self._open.remove(partitioner)
            partitioner.close()

    def _partition(
        self,
        records: Iterable[bytes],
        prefix: str,
        record_size: int,
        key_of: Callable[[bytes], Key],
        level: int,
    ) -> HashPartitioner:
        partitioner = HashPartitioner(
            self._directory, prefix, record_size, key_of, self._config, level
        )
        self._open.append(partitioner)
        partitioner.write(records)
        return partitioner

    def _join_in_memory(
        self, left_records: Iterable[bytes], right_records: Iterable[bytes]
    ) -> Iterator[Pair]:
        lefts = list(left_records)
        if not lefts and Unmatched.RIGHT not in self._unmatched:
            return
        table: dict[Key, list[int]] = {}
        for position, record in enumerate(lefts):
            key = self._left_key_of(record)
            if not _has_null(key):
                table.setdefault(key, []).append(position)
        paired = bytearray(len(lefts))
        for probe in right_records:
            key = self._right_key_of(probe)
            found = False
            for position in () if _has_null(key) else table.get(key, ()):
                if self._accepts(lefts[position], probe):
                    found = True
                    paired[position] = True
                    yield lefts[position], probe
            if not found and Unmatched.RIGHT in self._unmatched:
                yield None, probe
        if Unmatched.LEFT in self._unmatched:
            for position, record in enumerate(lefts):
                if not paired[position]:
                    yield record, None

    def _join_by_blocks(
        self, left_records: Iterable[bytes], right_records: Iterable[bytes], level: int
    ) -> Iterator[Pair]:
        """Reunión de una partición que el hash no pudo repartir.

        Ocurre cuando una misma clave tiene más filas de las que caben en el buffer: todas
        comparten hash, sea cual sea la función.
        """
        self.block_joins += 1
        with BlockNestedLoopJoin(
            self._directory / f"{BLOCKS_DIRECTORY}-{level}",
            self._left_record_size,
            self._right_record_size,
            self._same_key_and_accepted,
            self._config,
            self._unmatched,
        ) as joiner:
            yield from joiner.join(left_records, right_records)

    def _same_key_and_accepted(self, left: bytes, right: bytes) -> bool:
        key = self._left_key_of(left)
        if _has_null(key) or key != self._right_key_of(right):
            return False
        return self._accepts(left, right)


def _has_null(key: Key) -> bool:
    """Si la clave, o alguna de sus columnas cuando es compuesta, es NULL."""
    return key is None or (type(key) is tuple and None in key)
