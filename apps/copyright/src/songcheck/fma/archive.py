"""El audio de `fma_small.zip` (8 000 pistas, 7,7 GB), sacado pista a pista por rangos HTTP.

No se baja el `.zip`: se lee su índice del final, que da el tamaño y el CRC-32 de cada
pista, y después cada pista que falta con una sola petición. `zipfile` comprueba el CRC-32
al descomprimir, y una pista que ya está en disco se da por buena solo si mide y suma lo
que dice el índice: así la descarga se reanuda sin volver a pedir nada sano y sin dar por
buena una pista a medias o cambiada.
"""

from __future__ import annotations

import logging
import re
import threading
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from appkit.ranges import HttpRangeSource, RangeReader
from songcheck.catalog import is_intact, save_atomically
from songcheck.errors import TracksError
from songcheck.fma.tracks import Track

AUDIO_MEMBER = re.compile(r"fma_small/([0-9]{3})/([0-9]{6})\.mp3")
TRACK_ID_DIGITS = 6
FOLDER_DIGITS = 3
# Un fragmento de 30 s en MP3 pesa cientos de KB. `fma_small` trae tres pistas de un par de
# KB, sin audio que decodificar (099134, 108925 y 133297): se dejan fuera.
MIN_TRACK_BYTES = 100 * 1024
# Un miembro del `.zip` empieza con una cabecera local de 30 bytes más su nombre y su campo
# extra, que puede no medir lo mismo que en el índice central. Pedir este margen además del
# contenido trae la cabecera y el miembro en una sola petición.
LOCAL_HEADER_ALLOWANCE_BYTES = 1024
PROGRESS_EVERY_TRACKS = 250

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AudioEntry:
    """Una pista del `.zip`: su miembro y el tamaño y el CRC-32 que anota el índice."""

    track_id: int
    member: str
    size_bytes: int
    crc32: int

    @property
    def relative_path(self) -> str:
        """Su ruta dentro de la carpeta de pistas: `000/000002.mp3`."""
        return track_path(self.track_id)


@dataclass(frozen=True, slots=True)
class FetchReport:
    """Cuántas pistas se bajaron, con cuántos bytes de audio, y cuántas ya estaban sanas."""

    fetched: int
    fetched_bytes: int
    already_intact: int


def track_path(track_id: int) -> str:
    """Ruta de una pista en las carpetas de `fma_small`: `000/000002.mp3`."""
    name = f"{track_id:0{TRACK_ID_DIGITS}d}"
    return f"{name[:FOLDER_DIGITS]}/{name}.mp3"


def audio_index(archive: zipfile.ZipFile) -> dict[int, AudioEntry]:
    """Las pistas de `archive` por su id; los miembros que no son una pista se ignoran."""
    entries = {}
    for info in archive.infolist():
        match = AUDIO_MEMBER.fullmatch(info.filename)
        if match is None:
            continue
        track_id = int(match.group(2))
        entries[track_id] = AudioEntry(track_id, info.filename, info.file_size, info.CRC)
    return entries


def read_audio_index(audio_url: str) -> dict[int, AudioEntry]:
    """El índice de `fma_small.zip` remoto: unas pocas peticiones al final del archivo."""
    with HttpRangeSource(audio_url) as source, zipfile.ZipFile(RangeReader(source)) as archive:
        return audio_index(archive)


def playable_tracks(tracks: Iterable[Track], index: Mapping[int, AudioEntry]) -> list[Track]:
    """Las pistas que están en el `.zip` y pesan al menos `MIN_TRACK_BYTES`."""
    return [
        track
        for track in tracks
        if track.track_id in index and index[track.track_id].size_bytes >= MIN_TRACK_BYTES
    ]


def fetch_tracks(
    audio_url: str, entries: Sequence[AudioEntry], destination: Path, threads: int
) -> FetchReport:
    """Deja en `destination` cada pista de `entries` con su tamaño y su CRC-32. Baja solo
    las que faltan o no coinciden, repartidas entre `threads` hilos, cada uno con su propia
    conexión al `.zip`.

    Raises:
        TracksError: si una pista llega con otro CRC-32.
        appkit.download.DownloadError: si el servidor deja de entregar los rangos.
    """
    missing = [
        entry
        for entry in entries
        if not is_intact(destination / entry.relative_path, entry.size_bytes, entry.crc32)
    ]
    logger.info(
        "pistas: %d sanas en disco, %d por bajar con %d hilos",
        len(entries) - len(missing),
        len(missing),
        threads,
    )
    progress = _Progress(len(missing))
    batches = [missing[start::threads] for start in range(threads)]

    def extract(batch: Sequence[AudioEntry]) -> int:
        with HttpRangeSource(audio_url) as source:
            return _extract_tracks(RangeReader(source), batch, destination, progress)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        fetched = sum(pool.map(extract, [batch for batch in batches if batch]))
    return FetchReport(
        fetched=fetched,
        fetched_bytes=sum(entry.size_bytes for entry in missing),
        already_intact=len(entries) - len(missing),
    )


def _extract_tracks(
    reader: RangeReader, entries: Sequence[AudioEntry], destination: Path, progress: _Progress
) -> int:
    with zipfile.ZipFile(reader) as archive:
        for entry in entries:
            save_atomically(
                destination / entry.relative_path, _member_bytes(reader, archive, entry)
            )
            progress.advance()
    return len(entries)


def _member_bytes(reader: RangeReader, archive: zipfile.ZipFile, entry: AudioEntry) -> bytes:
    try:
        info = archive.getinfo(entry.member)
        reader.prefetch(
            info.header_offset,
            info.header_offset + LOCAL_HEADER_ALLOWANCE_BYTES + info.compress_size,
        )
        return archive.read(info)
    except KeyError as error:
        raise TracksError(f"{entry.member} no está en el .zip de audio") from error
    except zipfile.BadZipFile as error:
        raise TracksError(f"{entry.member} llegó dañada: {error}") from error


class _Progress:
    """Cuenta las pistas bajadas entre todos los hilos y lo anota cada
    `PROGRESS_EVERY_TRACKS`."""

    def __init__(self, total: int) -> None:
        self._total = total
        self._done = 0
        self._lock = threading.Lock()

    def advance(self) -> None:
        with self._lock:
            self._done += 1
            done = self._done
        if done % PROGRESS_EVERY_TRACKS == 0 or done == self._total:
            logger.info("pistas bajadas: %d de %d", done, self._total)
