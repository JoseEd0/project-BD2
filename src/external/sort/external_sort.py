"""Ordenamiento externo: ordenar más datos de los que caben en memoria.

Dos fases clásicas:

1. **Generación de runs.** Se leen tantos registros como quepan en el buffer, se ordenan en
   memoria y se vuelcan a un archivo temporal ya ordenado (un *run*).
2. **Mezcla k-vías.** Se abren `k` runs a la vez y se van sacando por un montículo los
   registros en orden. Si hay más runs que `k`, se repite por pasadas hasta que quede uno.

Ver `README.md` para el análisis de por qué el número de pasadas es logarítmico.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from types import TracebackType

from config import EngineConfig
from external.runs import RunReader, RunWriter
from index.keys import Key
from storage.page import slot_capacity

RUN_PREFIX = "run"
RUN_SUFFIX = ".tmp"


class ExternalSorter:
    """Ordena una secuencia de registros usando memoria acotada.

    Complejidad, con `N` registros, `B` registros en el buffer y abanico `k`:

    * runs iniciales: `⌈N/B⌉`;
    * pasadas de mezcla: `⌈log_k(N/B)⌉`;
    * coste total de E/S: `O(N · log_k(N/B))`;
    * memoria: `B` registros en la fase 1 y `k` páginas en la fase 2.
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
        self._buffer_records = config.sort_buffer_pages * slot_capacity(
            config.page_size, record_size
        )
        self._fan_in = max(2, config.merge_fan_in)
        self._temporary: list[Path] = []
        self._created = 0
        self.run_count = 0
        self.merge_passes = 0

    @property
    def fan_in(self) -> int:
        """Runs que la mezcla combina a la vez."""
        return self._fan_in

    @property
    def buffer_records(self) -> int:
        """Registros que la fase de generación mantiene en memoria a la vez."""
        return self._buffer_records

    def sort(self, records: Iterable[bytes]) -> Iterator[bytes]:
        """Devuelve los registros en orden de clave, apoyándose en disco."""
        runs = self._write_runs(records)
        self.run_count = len(runs)
        if not runs:
            return iter(())
        while len(runs) > self._fan_in:
            runs = self._merge_pass(runs)
            self.merge_passes += 1
        self.merge_passes += 1
        return self._merge_readers(runs)

    def close(self) -> None:
        """Borra todos los archivos temporales que quedaron."""
        for path in self._temporary:
            path.unlink(missing_ok=True)
        self._temporary.clear()

    def __enter__(self) -> ExternalSorter:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _write_runs(self, records: Iterable[bytes]) -> list[Path]:
        runs: list[Path] = []
        buffer: list[bytes] = []
        for record in records:
            buffer.append(record)
            if len(buffer) >= self._buffer_records:
                runs.append(self._spill(buffer))
                buffer = []
        if buffer:
            runs.append(self._spill(buffer))
        return runs

    def _spill(self, buffer: list[bytes]) -> Path:
        buffer.sort(key=self._key_of)
        path = self._new_temporary()
        with RunWriter(path, self._record_size, self._config) as writer:
            writer.extend(buffer)
        return path

    def _merge_pass(self, runs: list[Path]) -> list[Path]:
        merged: list[Path] = []
        for start in range(0, len(runs), self._fan_in):
            group = runs[start : start + self._fan_in]
            path = self._new_temporary()
            with RunWriter(path, self._record_size, self._config) as writer:
                writer.extend(self._merge_readers(group))
            merged.append(path)
            self._discard(group)
        return merged

    def _merge_readers(self, runs: list[Path]) -> Iterator[bytes]:
        """Mezcla k-vías: un montículo con el registro pendiente más pequeño de cada run."""
        readers = [RunReader(path, self._record_size, self._config) for path in runs]
        streams = [iter(reader) for reader in readers]
        heap: list[tuple[Key, int, bytes]] = []
        for index in range(len(streams)):
            self._push_next(heap, streams, index)
        try:
            while heap:
                _, index, record = heapq.heappop(heap)
                yield record
                self._push_next(heap, streams, index)
        finally:
            for reader in readers:
                reader.close()

    def _push_next(
        self, heap: list[tuple[Key, int, bytes]], streams: list[Iterator[bytes]], index: int
    ) -> None:
        record = next(streams[index], None)
        if record is not None:
            heapq.heappush(heap, (self._key_of(record), index, record))

    def _new_temporary(self) -> Path:
        """Nombre nunca reutilizado: reciclarlo pisaría un run que aún se está leyendo."""
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{RUN_PREFIX}{self._created}{RUN_SUFFIX}"
        self._created += 1
        self._temporary.append(path)
        return path

    def _discard(self, runs: list[Path]) -> None:
        for path in runs:
            path.unlink(missing_ok=True)
            if path in self._temporary:
                self._temporary.remove(path)
