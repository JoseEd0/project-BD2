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
from dataclasses import dataclass
from itertools import chain, islice
from pathlib import Path
from types import TracebackType
from typing import TypeVar

from config import EngineConfig
from external.joining import Pair, PairTest, Unmatched, any_pair
from external.loop import BlockNestedLoopJoin
from external.runs import RunReader, RunWriter, buffered_records, remove_if_empty
from hashing import canonical_key_bytes, stable_hash
from storage.types import Key

GROUP_PREFIX = "group"
LEFT_PREFIX = "left"
RIGHT_PREFIX = "right"
NULL_KEYS_SUFFIX = "-null.run"
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

    Es una agregación híbrida. Los primeros `B` grupos que aparecen se reducen en memoria
    hasta el final de la entrada; solo las filas de los grupos que llegan cuando ya no hay
    sitio se reparten en particiones, y cada partición se reduce después en memoria. Si una
    trae más grupos de los que caben, se vuelve a repartir con otra función de hash, las
    veces que haga falta: cada nivel divide sus grupos entre `P`.

    Complejidad, con `N` registros, `G` grupos, `P` particiones, `B` grupos en el buffer y
    `N'` registros de los grupos que no cupieron:

    * E/S: nada si `G ≤ B`; `2N'` si `G ≤ B + P · B`; en general `2N'` por nivel;
    * memoria: como mucho `2B` estados, los de memoria y los de la partición en curso.

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
        en que llegaron, partiendo de `start()`. Si todos los grupos caben en el buffer no
        se escribe ninguna partición: `levels` queda en cero.
        """
        groups: dict[bytes, tuple[Key, State]] = {}
        spilled = self._absorb(groups, records, start, step)
        first = next(spilled, None)
        if first is None:
            return iter(groups.values())
        partitioned = self._reduce_level(chain((first,), spilled), start, step, 0)
        return chain(partitioned, groups.values())

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
            groups = self._groups_of(partitioner.read(index), start, step)
            if groups is None:
                yield from self._reduce_level(partitioner.read(index), start, step, level + 1)
            else:
                yield from groups
        self._open.remove(partitioner)
        partitioner.close()

    def _absorb(
        self,
        groups: dict[bytes, tuple[Key, State]],
        records: Iterable[bytes],
        start: Callable[[], State],
        step: Callable[[State, bytes], State],
    ) -> Iterator[bytes]:
        """Reduce en `groups` las filas de los grupos que caben y deja pasar las de los que
        llegan cuando ya está lleno. Los grupos se distinguen como en `_groups_of`."""
        for record in records:
            key = self._key_of(record)
            signature = canonical_key_bytes(key)
            group = groups.get(signature)
            if group is None:
                if len(groups) == self._memory_groups:
                    yield record
                    continue
                group = (key, start())
            groups[signature] = (group[0], step(group[1], record))

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

    def _groups_of(
        self,
        records: Iterable[bytes],
        start: Callable[[], State],
        step: Callable[[State, bytes], State],
    ) -> list[tuple[Key, State]] | None:
        """Clave y estado de cada grupo de una partición, o `None` si no caben.

        Los grupos se distinguen por los mismos bytes con que se reparten. Así, todas las
        filas de una partición que no se puede volver a separar forman un solo grupo, que
        siempre cabe: repartir termina. Distinguirlos por la igualdad de Python podría
        dejar en una misma partición más grupos de los que caben sin que ningún hash los
        separara nunca.
        """
        groups: dict[bytes, tuple[Key, State]] = {}
        for record in records:
            key = self._key_of(record)
            signature = canonical_key_bytes(key)
            group = groups.get(signature)
            if group is None:
                if len(groups) == self._memory_groups:
                    return None
                group = (key, start())
            groups[signature] = (group[0], step(group[1], record))
        return list(groups.values())


class ExternalHashJoin:
    """Junta dos entradas por igualdad de clave (*grace hash join*).

    Se particionan **las dos** entradas con el mismo hash. Como las claves iguales caen en
    la misma partición, basta con cargar en memoria uno de los dos lados de cada partición
    y recorrer el otro comparando. Se carga la izquierda si cabe en el buffer y, si no, la
    derecha. Si una entrada entera cabe, no se particiona nada: se carga y la otra se
    recorre una sola vez, así que juntar una tabla grande con una pequeña no toca el disco
    sea cual sea el orden en que se escribieron.

    Una partición en la que no cabe ninguno de los dos lados se vuelve a repartir, junto
    con su pareja, con otra función de hash. Si la nueva función tampoco separa nada es
    que sus filas comparten clave, y no hay hash que las reparta: esa partición se resuelve
    por bucles anidados en bloques, que no necesita tenerla entera en memoria.

    Una clave con NULL no es igual a ninguna, ni a otro NULL: esas filas nunca emparejan.
    Por eso no se reparten. Repartirlas las juntaría todas en una partición —comparten
    hash— que, si fueran muchas, habría que reunir por bloques comparando cada una con
    todas las de enfrente para nada. Se apartan al pasar y, si la reunión las conserva,
    salen al final sin pareja.

    `accepts` añade condiciones a la igualdad de claves, y `unmatched` dice qué filas
    sin pareja se entregan de todos modos, que es lo que distingue un `LEFT`, un `RIGHT`
    o un `FULL JOIN` de uno interno.

    Complejidad, con `N` y `M` registros y `B` registros en el buffer:

    * E/S: `2(N + M)` si las particiones caben, y otro tanto por cada nivel extra;
    * memoria: como mucho `B` registros del lado que se carga; mientras se decide cuál, una
      muestra de hasta `B` de cada lado;
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
        self._right_memory_records = buffered_records(config, right_record_size)
        self._open: list[HashPartitioner] = []
        self._set_aside = [
            directory / f"{LEFT_PREFIX}{NULL_KEYS_SUFFIX}",
            directory / f"{RIGHT_PREFIX}{NULL_KEYS_SUFFIX}",
        ]
        self.levels = 0
        self.block_joins = 0

    def join(self, left_records: Iterable[bytes], right_records: Iterable[bytes]) -> Iterator[Pair]:
        """Pares `(fila izquierda, fila derecha)` con la misma clave, y las filas sin pareja
        que la reunión conserva, con `None` en el otro lado."""
        self._directory.mkdir(parents=True, exist_ok=True)
        left_path, right_path = self._set_aside
        with (
            RunWriter(left_path, self._left_record_size, self._config) as left_nulls,
            RunWriter(right_path, self._right_record_size, self._config) as right_nulls,
        ):
            lefts = _keyed_only(
                left_records, self._left_key_of, self._keeper(Unmatched.LEFT, left_nulls)
            )
            rights = _keyed_only(
                right_records, self._right_key_of, self._keeper(Unmatched.RIGHT, right_nulls)
            )
            head = list(islice(lefts, self._memory_records + 1))
            if len(head) <= self._memory_records:
                yield from self._join_in_memory(head, rights)
            else:
                right_head = list(islice(rights, self._right_memory_records + 1))
                # Las muestras ya van dentro de los flujos: soltarlas para no tenerlas en
                # memoria junto a cada partición que se cargue.
                stream, right_stream = chain(head, lefts), chain(right_head, rights)
                right_fits = len(right_head) <= self._right_memory_records
                del head, right_head
                if right_fits:
                    yield from self._join_with_right_in_memory(stream, right_stream)
                else:
                    yield from self._join_level(stream, right_stream, 0)
        with RunReader(left_path, self._left_record_size, self._config) as left_unmatched:
            yield from ((record, None) for record in left_unmatched)
        with RunReader(right_path, self._right_record_size, self._config) as right_unmatched:
            yield from ((None, record) for record in right_unmatched)

    def close(self) -> None:
        while self._open:
            self._open.pop().close()
        for path in self._set_aside:
            path.unlink(missing_ok=True)
        remove_if_empty(self._directory)

    def _keeper(self, side: Unmatched, writer: RunWriter) -> Callable[[bytes], None]:
        """Qué hacer con una fila de clave NULL de ese lado: guardarla, si la reunión
        conserva sus filas sin pareja, o descartarla."""
        return writer.append if side in self._unmatched else _discard

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
        for index, (size, right_size) in enumerate(
            zip(left.partition_sizes, right.partition_sizes, strict=True)
        ):
            lefts, rights = left.read(index), right.read(index)
            if size <= self._memory_records:
                yield from self._join_in_memory(lefts, rights)
            elif right_size <= self._right_memory_records:
                yield from self._join_with_right_in_memory(lefts, rights)
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
        """Carga la izquierda y recorre la derecha."""
        yield from _build_and_probe(
            _Side(list(left_records), self._left_key_of, Unmatched.LEFT in self._unmatched),
            _Side(right_records, self._right_key_of, Unmatched.RIGHT in self._unmatched),
            self._accepts,
        )

    def _join_with_right_in_memory(
        self, left_records: Iterable[bytes], right_records: Iterable[bytes]
    ) -> Iterator[Pair]:
        """Carga la derecha y recorre la izquierda; los pares salen igual, izquierda primero."""
        swapped = _build_and_probe(
            _Side(list(right_records), self._right_key_of, Unmatched.RIGHT in self._unmatched),
            _Side(left_records, self._left_key_of, Unmatched.LEFT in self._unmatched),
            lambda right, left: self._accepts(left, right),
        )
        yield from ((left, right) for right, left in swapped)

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
        if self._left_key_of(left) != self._right_key_of(right):
            return False
        return self._accepts(left, right)


@dataclass(frozen=True, slots=True)
class _Side:
    """Un lado de la reunión en memoria: sus filas, cómo sacar su clave y si sus filas sin
    pareja se conservan."""

    records: Iterable[bytes]
    key_of: Callable[[bytes], Key]
    keeps_unmatched: bool


def _build_and_probe(build: _Side, probe: _Side, accepts: PairTest) -> Iterator[Pair]:
    """Tabla hash sobre `build` (una lista) y una pasada por `probe`. Los pares salen
    `(fila de build, fila de probe)`; `accepts` los recibe en ese mismo orden."""
    built = list(build.records)
    if not built and not probe.keeps_unmatched:
        return
    table: dict[Key, list[int]] = {}
    for position, record in enumerate(built):
        table.setdefault(build.key_of(record), []).append(position)
    paired = bytearray(len(built))
    for record in probe.records:
        found = False
        for position in table.get(probe.key_of(record), ()):
            if accepts(built[position], record):
                found = True
                paired[position] = True
                yield built[position], record
        if not found and probe.keeps_unmatched:
            yield None, record
    if build.keeps_unmatched:
        for position, record in enumerate(built):
            if not paired[position]:
                yield record, None


def _keyed_only(
    records: Iterable[bytes], key_of: Callable[[bytes], Key], set_aside: Callable[[bytes], None]
) -> Iterator[bytes]:
    """Las filas cuya clave no tiene NULL; las demás se entregan a `set_aside` al pasar."""
    for record in records:
        if _has_null(key_of(record)):
            set_aside(record)
        else:
            yield record


def _discard(_: bytes) -> None:
    """Para las filas de clave NULL que una reunión no conserva."""


def _has_null(key: Key) -> bool:
    """Si la clave, o alguna de sus columnas cuando es compuesta, es NULL."""
    return key is None or (type(key) is tuple and None in key)
