"""Consulta por segmentos de un audio e índices sobre tramos (issue #74), con pistas de FMA.

    .venv/bin/python -m benchmarks.segments_benchmark \\
        --tracks-directory data/datasets/fma_small/pistas

Responde dos preguntas, siempre de punta a punta por el motor:

1. **Consultar una canción por segmentos.** `POST /media/segments` sube la canción una vez,
   la decodifica una vez y busca todas sus ventanas en un solo lote. Sin él, un cliente
   subiría la canción (`POST /media/queries`) y lanzaría un `SIMILAR_TO('cancion#t=a,b', k)`
   por ventana, cada uno con su petición, su decodificación del tramo y su búsqueda. Las
   canciones son pistas del catálogo pegadas hasta durar `--song-seconds` (30 s a 3 min),
   en MP3 a 44,1 kHz como las de FMA; se consultan contra una tabla de tramos de
   `--window` s cada `--hop` s con su índice HNSW. Cada medida es la mediana de
   `--repetitions` y, de paso, se comprueba que los dos caminos dan los mismos vecinos en
   cada ventana.
2. **Indexar tramos.** `CREATE INDEX ... USING HNSW` sobre las pistas enteras frente a la
   misma colección cortada en tramos, para `--catalogue-sizes` pistas; y la extracción de
   esos tramos leyendo cada uno por su cuenta frente a una decodificación por pista, que es
   lo que hace el índice.

La extracción usa `--workers` procesos al construir (uno por omisión, para que las cifras
no dependan de cuántos núcleos libres había) y la de la segunda comparación corre en este
proceso.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import soundfile
from fastapi.testclient import TestClient

from api.main import create_app
from benchmarks.common import Report, machine_description, results_directory, time_it
from benchmarks.segments.names import (
    DECODE,
    EXTRACT,
    EXTRACTION,
    FRAGMENTS,
    GROUPED,
    INDEX_BUILD,
    PER_ROW,
    REPORT,
    SEARCH,
    SEGMENT_QUERY,
    SEGMENT_STAGES,
    SEGMENTS,
    SEPARATE,
    WHOLE_FILES,
)
from config import EngineConfig
from multimedia import (
    MediaReference,
    TemporalFragment,
    audio_duration,
    decode_audio,
    extract_each,
    extract_features,
    segment_windows,
)
from query.engine import Engine
from query.plan import MILLISECONDS_PER_SECOND

DEFAULT_CATALOGUE_SIZES = (50, 100, 200)
DEFAULT_SONG_SECONDS = (30, 60, 120, 180)
DEFAULT_WINDOW = 10.0
DEFAULT_HOP = 5.0
DEFAULT_K = 5
DEFAULT_REPETITIONS = 3
DEFAULT_WORKERS = 1
TRACK_PATTERN = "*/*.mp3"
# La frecuencia y el formato de una pista de FMA.
SONG_SAMPLE_RATE = 44_100
SONG_FORMAT = "MP3"
SONG_SUFFIX = ".mp3"
WHOLE_TABLE = "pistas"
FRAGMENT_TABLE = "tramos"
SEGMENTS_ROUTE = "/media/segments"
UPLOAD_ROUTE = "/media/queries"
QUERY_ROUTE = "/query"
OK = 200
SCORE_DIGITS = 9


@dataclass(frozen=True, slots=True)
class Settings:
    tracks_directory: Path
    catalogue_sizes: tuple[int, ...]
    song_seconds: tuple[int, ...]
    window: float
    hop: float
    k: int
    repetitions: int
    workers: int


@dataclass(frozen=True, slots=True)
class SongQuery:
    """Lo que devolvió un camino para una canción: los vecinos de cada ventana, con su
    puntaje, y la mediana de lo que tardó."""

    neighbours: list[list[tuple[int, float]]]
    milliseconds: float
    stages: dict[str, float]


def run(settings: Settings, workspace: Path) -> Report:
    tracks = _tracks(settings)
    report = Report(REPORT, _parameters(settings, tracks))
    largest = max(settings.catalogue_sizes)
    for size in sorted(settings.catalogue_sizes):
        directory = workspace / f"catalogo-{size}"
        _measure_builds(report, settings, tracks[:size], directory)
        _measure_extraction(report, settings, tracks[:size])
        print(f"catálogo de {size} pistas medido")
    _measure_songs(report, settings, tracks[:largest], workspace / f"catalogo-{largest}")
    return report


def _tracks(settings: Settings) -> list[Path]:
    found = sorted(settings.tracks_directory.resolve().glob(TRACK_PATTERN))
    needed = max(settings.catalogue_sizes)
    if len(found) < needed:
        raise SystemExit(
            f"hacen falta {needed} pistas y {settings.tracks_directory} tiene {len(found)}"
        )
    return found[:needed]


def _config(settings: Settings, directory: Path) -> EngineConfig:
    return EngineConfig(
        data_directory=directory,
        import_directories=(settings.tracks_directory.resolve(),),
        feature_workers=settings.workers,
        segment_window_seconds=settings.window,
        segment_hop_seconds=settings.hop,
    )


def _fragments(settings: Settings, tracks: list[Path]) -> list[MediaReference]:
    config = _config(settings, Path())
    return [
        MediaReference(track, window)
        for track in tracks
        for window in segment_windows(audio_duration(track), settings.window, settings.hop, config)
    ]


def _measure_builds(
    report: Report, settings: Settings, tracks: list[Path], directory: Path
) -> None:
    fragments = _fragments(settings, tracks)
    with Engine(_config(settings, directory)) as engine:
        whole_ms = _build(engine, WHOLE_TABLE, [MediaReference(track) for track in tracks])
        fragment_ms = _build(engine, FRAGMENT_TABLE, fragments)
    size = len(tracks)
    report.add(WHOLE_FILES, INDEX_BUILD, size, whole_ms, filas=size, decodificaciones=size)
    report.add(
        FRAGMENTS, INDEX_BUILD, size, fragment_ms, filas=len(fragments), decodificaciones=size
    )


def _build(engine: Engine, table: str, references: list[MediaReference]) -> float:
    engine.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY, ruta VARCHAR(250))")
    values = ", ".join(
        f"({key}, '{_written(reference)}')" for key, reference in enumerate(references)
    )
    engine.execute(f"INSERT INTO {table} VALUES {values}")
    index = f"idx_{table}"
    return time_it(lambda: engine.execute(f"CREATE INDEX {index} ON {table} USING HNSW (ruta)"))


def _written(reference: MediaReference) -> str:
    if reference.fragment is None:
        return str(reference.path)
    return f"{reference.path}#{_fragment_text(reference.fragment)}"


def _fragment_text(fragment: TemporalFragment) -> str:
    return f"t={fragment.start:g},{fragment.end:g}"


def _measure_extraction(report: Report, settings: Settings, tracks: list[Path]) -> None:
    fragments = _fragments(settings, tracks)
    config = _config(settings, Path())
    _warm_up(fragments[0], config)
    per_row = time_it(
        lambda: [extract_features(item.path, config, item.fragment) for item in fragments]
    )
    grouped = time_it(lambda: list(extract_each(fragments, config)))
    size = len(tracks)
    report.add(PER_ROW, EXTRACTION, size, per_row, filas=len(fragments), lecturas=len(fragments))
    report.add(GROUPED, EXTRACTION, size, grouped, filas=len(fragments), lecturas=size)


def _warm_up(reference: MediaReference, config: EngineConfig) -> None:
    """La primera llamada a librosa en un proceso carga sus funciones compiladas con numba:
    se paga antes de medir, para no cargársela al primer camino."""
    extract_features(reference.path, config, reference.fragment)


def _measure_songs(
    report: Report, settings: Settings, tracks: list[Path], directory: Path
) -> None:
    config = _config(settings, directory)
    with TestClient(create_app(config)) as client:
        for seconds in settings.song_seconds:
            song = _song(tracks, seconds, directory)
            windows = segment_windows(audio_duration(song), settings.window, settings.hop, config)
            batched = _repeated(settings, partial(_by_segments, client, settings, song))
            separate = _repeated(settings, partial(_one_by_one, client, settings, song, windows))
            _add_song(report, seconds, len(windows), batched, separate)
            print(f"canción de {seconds} s medida")


def _song(tracks: list[Path], seconds: int, directory: Path) -> Path:
    """Pistas del catálogo pegadas, mezcladas a mono y a 44,1 kHz, hasta durar `seconds`."""
    wanted = seconds * SONG_SAMPLE_RATE
    pieces: list[np.ndarray] = []
    for track in tracks:
        if sum(len(piece) for piece in pieces) >= wanted:
            break
        audio = decode_audio(track)
        pieces.append(
            librosa.resample(audio.samples, orig_sr=audio.sample_rate, target_sr=SONG_SAMPLE_RATE)
        )
    path = directory / f"cancion-{seconds}s{SONG_SUFFIX}"
    soundfile.write(path, np.concatenate(pieces)[:wanted], SONG_SAMPLE_RATE, format=SONG_FORMAT)
    return path


def _repeated(settings: Settings, query: Callable[[], SongQuery]) -> SongQuery:
    runs = [query() for _ in range(settings.repetitions)]
    stages = {
        name: statistics.median(found.stages[name] for found in runs) for name in runs[0].stages
    }
    return SongQuery(
        runs[-1].neighbours, statistics.median(found.milliseconds for found in runs), stages
    )


def _by_segments(client: TestClient, settings: Settings, song: Path) -> SongQuery:
    payload = song.read_bytes()
    started = time.perf_counter()
    response = client.post(
        SEGMENTS_ROUTE,
        data={
            "table": FRAGMENT_TABLE,
            "k": str(settings.k),
            "window": str(settings.window),
            "hop": str(settings.hop),
        },
        files={"file": (song.name, payload, "audio/mpeg")},
    )
    elapsed = _since(started)
    found = _ok(response)
    neighbours = [
        [(item["row"]["id"], item["score"]) for item in segment["neighbours"]]
        for segment in found["segments"]
    ]
    return SongQuery(neighbours, elapsed, dict(found["timings"]))


def _one_by_one(
    client: TestClient, settings: Settings, song: Path, windows: tuple[TemporalFragment, ...]
) -> SongQuery:
    payload = song.read_bytes()
    started = time.perf_counter()
    uploaded = _ok(client.post(UPLOAD_ROUTE, files={"file": (song.name, payload, "audio/mpeg")}))
    neighbours = []
    for window in windows:
        sql = (
            f"SELECT id, SIMILARITY_SCORE() FROM {FRAGMENT_TABLE} "
            f"WHERE SIMILAR_TO('{uploaded['path']}#{_fragment_text(window)}', k = {settings.k})"
        )
        rows = _ok(client.post(QUERY_ROUTE, json={"sql": sql}))["rows"]
        neighbours.append([(row[0], row[1]) for row in rows])
    return SongQuery(neighbours, _since(started), {})


def _add_song(
    report: Report, seconds: int, windows: int, batched: SongQuery, separate: SongQuery
) -> None:
    same, difference = _agreement(batched, separate)
    report.add(
        SEGMENTS,
        SEGMENT_QUERY,
        seconds,
        batched.milliseconds,
        ventanas=windows,
        peticiones=1,
        ms_por_ventana=round(batched.milliseconds / windows, 2),
        ventanas_con_los_mismos_vecinos=same,
        maxima_diferencia_de_puntaje=difference,
    )
    report.add(
        SEPARATE,
        SEGMENT_QUERY,
        seconds,
        separate.milliseconds,
        ventanas=windows,
        peticiones=windows + 1,
        ms_por_ventana=round(separate.milliseconds / windows, 2),
    )
    for stage, name in (("decode_ms", DECODE), ("extraction_ms", EXTRACT), ("search_ms", SEARCH)):
        report.add(name, SEGMENT_STAGES, seconds, batched.stages[stage], ventanas=windows)


def _agreement(batched: SongQuery, separate: SongQuery) -> tuple[int, float]:
    """Ventanas con los mismos vecinos en el mismo orden y la mayor diferencia de puntaje."""
    same = 0
    difference = 0.0
    for mine, theirs in zip(batched.neighbours, separate.neighbours, strict=True):
        same += [key for key, _ in mine] == [key for key, _ in theirs]
        difference = max(
            [difference, *(abs(a - b) for (_, a), (_, b) in zip(mine, theirs, strict=False))]
        )
    return same, round(difference, SCORE_DIGITS)


def _ok(response: Any) -> dict[str, Any]:
    if response.status_code != OK:
        raise SystemExit(f"el API respondió {response.status_code}: {response.text}")
    found: dict[str, Any] = response.json()
    return found


def _since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _parameters(settings: Settings, tracks: list[Path]) -> dict[str, Any]:
    defaults = EngineConfig()
    return {
        "pistas": f"las primeras {len(tracks)} de {settings.tracks_directory.name}, por ruta",
        "catalogos": list(settings.catalogue_sizes),
        "canciones_s": list(settings.song_seconds),
        "cancion": f"pistas del catálogo pegadas, mono, {SONG_SAMPLE_RATE} Hz, {SONG_FORMAT}",
        "ventana_s": settings.window,
        "salto_s": settings.hop,
        "k": settings.k,
        "indice": "HNSW con los parámetros por omisión (coseno)",
        "vocabulario": defaults.vocabulary_size,
        "repeticiones": settings.repetitions,
        "procesos_de_extraccion": settings.workers,
        "versiones": {"librosa": librosa.__version__, "soundfile": soundfile.__version__},
        "maquina": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks-directory", type=Path, required=True)
    parser.add_argument(
        "--catalogue-sizes", type=int, nargs="+", default=list(DEFAULT_CATALOGUE_SIZES)
    )
    parser.add_argument("--song-seconds", type=int, nargs="+", default=list(DEFAULT_SONG_SECONDS))
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW)
    parser.add_argument("--hop", type=float, default=DEFAULT_HOP)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    settings = Settings(
        tracks_directory=arguments.tracks_directory,
        catalogue_sizes=tuple(arguments.catalogue_sizes),
        song_seconds=tuple(arguments.song_seconds),
        window=arguments.window,
        hop=arguments.hop,
        k=arguments.k,
        repetitions=arguments.repetitions,
        workers=arguments.workers,
    )
    workspace = Path(tempfile.mkdtemp(prefix="minigestor-segmentos-"))
    try:
        report = run(settings, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    path = report.save(arguments.output)
    print(report.render())
    print(f"\nResultados crudos en {path}")


if __name__ == "__main__":
    main()
