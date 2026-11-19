"""La detección de copyright (issue #76): latencia de `POST /detections` y casos de control.

    uv run --no-sync python -m benchmarks.copyright_detection_benchmark \\
        --engine-dir data/copyright-engine --data-dir data/datasets

**El gestor es una copia** del que dejó el trabajo de carga con el catálogo entero (7 994
canciones de `fma_small`, 83 290 tramos con su HNSW), servida por uvicorn en otro proceso y
en un puerto libre; el original no se toca. De la copia se borran los tramos de
`--held-out` canciones al azar (con `--seed`): son música real, del mismo FMA, que el
catálogo ya no tiene, y hacen de **pistas no catalogadas**. El servicio corre en este
proceso con su configuración por omisión y habla con el gestor por HTTP.

1. **Latencia.** Para cada duración de `--durations`, `--repetitions` subidas distintas, cada
   una pistas de 30 s pegadas (una del catálogo, una no catalogada, y así) y codificadas en
   MP3 a 44,1 kHz estéreo como las de FMA. Se mide la petición entera desde el cliente y las
   etapas de `Server-Timing`; se anota la carga media antes de cada duración, porque la
   máquina puede estar ocupada. De paso se cuenta cuántas pistas del catálogo se
   encontraron con su desfase.
2. **Casos de control.** `--cases` veces cada uno: una pista del catálogo entera, un tramo
   de 15 s fuera de la rejilla de 2,5 s metido dentro de una pista no catalogada, uno de
   7,5 s (la copia más corta que llena una ventana) y una pista no catalogada sola. Se guarda
   el puntaje de la canción copiada y el mejor de cualquier otra: el umbral provisional sale
   de dónde quedan unos y otros.
"""

from __future__ import annotations

import argparse
import io
import math
import random
import shutil
import statistics
import tempfile
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile
from fastapi.testclient import TestClient
from pydantic import SecretStr

from api.settings import IMPORT_DIRECTORIES_VARIABLE
from benchmarks.common import Report, load_average, machine_description, results_directory
from benchmarks.engine_process import engine_server
from minigestor_client import Client
from query.plan import MILLISECONDS_PER_SECOND
from songcheck.api import build_app
from songcheck.catalog import CatalogSong, dataset_directory, read_catalog
from songcheck.detection_api import (
    DETECTIONS_PATH,
    ENGINE_DECODE_STAGE,
    ENGINE_EXTRACTION_STAGE,
    ENGINE_SEARCH_STAGE,
    LOUDNESS_STAGE,
    PROBE_STAGE,
    SEGMENTS_STAGE,
    SONGS_STAGE,
    VOTING_STAGE,
)
from songcheck.schema import SEGMENTS_TABLE
from songcheck.seed import SEGMENT_ID_STRIDE
from songcheck.settings import CopyrightSettings
from songcheck.windows import WINDOWING

REPORT_NAME = "copyright_deteccion"
LATENCY = "latencia"
STAGES = "etapas"
CONTROL = "control"
SCORES = "puntajes"
P50 = "p50"
P95 = "p95"
MINIMUM = "mínimo"
MEDIAN = "mediana"
MAXIMUM = "máximo"
WHOLE_COPY = "pista del catálogo entera"
LONG_FRAGMENT = "tramo de 15 s fuera de la rejilla"
SHORT_FRAGMENT = "tramo de 7,5 s"
UNCATALOGUED = "pista no catalogada"
OTHER_SONGS = "otras canciones"
COPY_KINDS = (WHOLE_COPY, LONG_FRAGMENT, SHORT_FRAGMENT)
MEASURED_STAGES = (
    PROBE_STAGE,
    LOUDNESS_STAGE,
    SEGMENTS_STAGE,
    ENGINE_DECODE_STAGE,
    ENGINE_EXTRACTION_STAGE,
    ENGINE_SEARCH_STAGE,
    VOTING_STAGE,
    SONGS_STAGE,
)
DEFAULT_DURATIONS = (30, 60, 180)
DEFAULT_REPETITIONS = 10
DEFAULT_CASES = 20
DEFAULT_HELD_OUT = 40
DEFAULT_SEED = 20261119
PERCENTILES = (50, 95)
UPLOAD_RATE = 44_100
UPLOAD_CHANNELS = 2
UPLOAD_FORMAT = "MP3"
UPLOAD_NAME = "subida.mp3"
LONG_FRAGMENT_SPAN = (6.0, 21.0)
LONG_FRAGMENT_AT = 10.0
SHORT_FRAGMENT_SPAN = (10.0, 17.5)
SHORT_FRAGMENT_AT = 5.0
SERVICE_URL = "http://servicio"
NO_KEY = SecretStr("sin-claves-en-el-gestor-del-experimento")
# Una detección de 3 min contra el catálogo entero tarda segundos; con la máquina ocupada,
# más. El plazo por omisión del servicio (45 s) se deja como en producción.
DIGITS = 3
# Las pistas de fma_small duran de 29,96 a 30,00 s decodificadas.
WHOLE_SECONDS = 29.9
# Una copia que no llena dos ventanas seguidas no puede hacer una racha.
SHORTEST_COPY_SECONDS = WINDOWING.window_seconds + WINDOWING.hop_seconds


@dataclass(frozen=True, slots=True)
class Upload:
    """Un audio de prueba y las copias que contiene: (canción, desfase en segundos)."""

    content: bytes
    seconds: float
    copies: tuple[tuple[int, float], ...]


class Tracks:
    """Las pistas de FMA decodificadas a 44,1 kHz estéreo, leídas una vez cada una."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[int, np.ndarray] = {}

    def samples(self, song: CatalogSong) -> np.ndarray:
        if song.track_id not in self._cache:
            data, _ = soundfile.read(self._root / song.path, dtype="float32", always_2d=True)
            self._cache[song.track_id] = _stereo(data)
        return self._cache[song.track_id]


def eligible_songs(songs: Iterable[CatalogSong], root: Path, count: int) -> list[CatalogSong]:
    """Las primeras `count` pistas a 44,1 kHz que duran una pista entera de FMA: se pegan sin
    remuestrear."""
    chosen: list[CatalogSong] = []
    for song in songs:
        if len(chosen) == count:
            break
        if song.duration_seconds < WHOLE_SECONDS:
            continue
        if soundfile.info(str(root / song.path)).samplerate == UPLOAD_RATE:
            chosen.append(song)
    return chosen


def encode(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, UPLOAD_RATE, format=UPLOAD_FORMAT)
    return buffer.getvalue()


def concatenated(
    tracks: Tracks, catalogued: Iterator[CatalogSong], others: Iterator[CatalogSong], seconds: int
) -> Upload:
    """Pistas de 30 s pegadas hasta `seconds`: una del catálogo, una no catalogada, …"""
    parts: list[np.ndarray] = []
    copies: list[tuple[int, float]] = []
    position = 0
    for index in range(_clips(seconds)):
        song = next(catalogued) if index % 2 == 0 else next(others)
        clip = tracks.samples(song)[: _frames(WHOLE_SECONDS)]
        starts = position / UPLOAD_RATE
        if index % 2 == 0 and seconds - starts >= SHORTEST_COPY_SECONDS:
            copies.append((song.track_id, starts))
        parts.append(clip)
        position += len(clip)
    samples = np.concatenate(parts)[: _frames(seconds)]
    return Upload(encode(samples), seconds, tuple(copies))


def embedded(
    tracks: Tracks, copied: CatalogSong, host: CatalogSong, span: tuple[float, float], at: float
) -> Upload:
    """El tramo `span` de `copied` pegado encima de `host` a partir de `at` segundos."""
    samples = tracks.samples(host)[: _frames(WHOLE_SECONDS)].copy()
    fragment = tracks.samples(copied)[_frames(span[0]) : _frames(span[1])]
    samples[_frames(at) : _frames(at) + len(fragment)] = fragment
    return Upload(encode(samples), WHOLE_SECONDS, ((copied.track_id, at - span[0]),))


def whole(tracks: Tracks, song: CatalogSong, catalogued: bool) -> Upload:
    copies = ((song.track_id, 0.0),) if catalogued else ()
    return Upload(encode(tracks.samples(song)[: _frames(WHOLE_SECONDS)]), WHOLE_SECONDS, copies)


@contextmanager
def engine_copy(source: Path, workspace: Path | None, data_directory: Path) -> Iterator[str]:
    """La URL de un gestor sobre una copia de `source`, que se borra al salir."""
    with tempfile.TemporaryDirectory(dir=workspace) as scratch:
        copy = Path(scratch) / "gestor"
        shutil.copytree(source, copy)
        environment = {IMPORT_DIRECTORIES_VARIABLE: str(data_directory.resolve())}
        with engine_server(copy, environment) as url:
            yield url


def hold_out(url: str, songs: Sequence[CatalogSong]) -> None:
    """Borra de la copia los tramos de `songs`: dejan de estar en el catálogo."""
    with Client(url) as engine:
        for song in songs:
            first = song.track_id * SEGMENT_ID_STRIDE
            engine.execute(
                f"DELETE FROM {SEGMENTS_TABLE} WHERE id BETWEEN $1 AND $2",
                [first, first + SEGMENT_ID_STRIDE - 1],
            )


def service_settings(engine_url: str) -> CopyrightSettings:
    """La configuración por omisión del servicio, hablando con `engine_url`."""
    return CopyrightSettings.model_validate({"engine_url": engine_url, "engine_key": NO_KEY})


@contextmanager
def service(url: str) -> Iterator[TestClient]:
    settings = service_settings(url)
    with TestClient(build_app(settings), base_url=SERVICE_URL) as client:
        yield client


def detect(client: TestClient, upload: Upload) -> tuple[float, dict[str, Any], dict[str, float]]:
    """Milisegundos de la petición, la detección y las etapas de `Server-Timing`."""
    started = time.perf_counter()
    response = client.post(DETECTIONS_PATH, files={"file": (UPLOAD_NAME, upload.content)})
    elapsed = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    response.raise_for_status()
    return elapsed, response.json(), _server_timing(response.headers["Server-Timing"])


def found_copies(detection: dict[str, Any], upload: Upload) -> int:
    """Las copias de la subida detectadas con su canción y su desfase (a un salto o menos)."""
    detected = [
        (match["song"]["id"], match["offset_seconds"])
        for match in detection["matches"]
        if match["above_threshold"]
    ]
    return sum(
        any(song == found and abs(offset - at) <= WINDOWING.hop_seconds for found, at in detected)
        for song, offset in upload.copies
    )


def measure_latency(
    report: Report,
    client: TestClient,
    tracks: Tracks,
    catalogued: Sequence[CatalogSong],
    others: Sequence[CatalogSong],
    arguments: argparse.Namespace,
) -> None:
    catalogued_cycle = _cycle(catalogued)
    others_cycle = _cycle(others)
    for seconds in arguments.durations:
        uploads = [
            concatenated(tracks, catalogued_cycle, others_cycle, seconds)
            for _ in range(arguments.repetitions)
        ]
        report.parameters[f"load_average_{seconds}"] = load_average()
        timings = [detect(client, upload) for upload in uploads]
        totals = [elapsed for elapsed, _, _ in timings]
        expected = sum(len(upload.copies) for upload in uploads)
        found = sum(
            found_copies(detection, upload)
            for upload, (_, detection, _) in zip(uploads, timings, strict=True)
        )
        windows = timings[0][1]["windows"]
        for name, value in zip((P50, P95), np.percentile(totals, PERCENTILES), strict=True):
            report.add(
                name,
                LATENCY,
                seconds,
                round(float(value), DIGITS),
                ventanas=windows,
                ms_por_ventana=round(float(value) / windows, DIGITS),
                copias=expected,
                encontradas=found,
            )
        for stage in MEASURED_STAGES:
            values = [stages.get(stage, 0.0) for _, _, stages in timings]
            report.add(stage, STAGES, seconds, round(statistics.median(values), DIGITS))


def measure_controls(
    report: Report,
    client: TestClient,
    tracks: Tracks,
    catalogued: Sequence[CatalogSong],
    others: Sequence[CatalogSong],
    cases: int,
) -> dict[str, list[float]]:
    scores: dict[str, list[float]] = {kind: [] for kind in (*COPY_KINDS, UNCATALOGUED, OTHER_SONGS)}
    for index in range(cases):
        copied, host = catalogued[index], others[index]
        uploads = {
            WHOLE_COPY: whole(tracks, copied, catalogued=True),
            LONG_FRAGMENT: embedded(tracks, copied, host, LONG_FRAGMENT_SPAN, LONG_FRAGMENT_AT),
            SHORT_FRAGMENT: embedded(tracks, copied, host, SHORT_FRAGMENT_SPAN, SHORT_FRAGMENT_AT),
            UNCATALOGUED: whole(tracks, host, catalogued=False),
        }
        for kind, upload in uploads.items():
            elapsed, detection, _ = detect(client, upload)
            own, other, offset = _scores(detection, copied.track_id)
            if kind in COPY_KINDS:
                scores[kind].append(own)
                scores[OTHER_SONGS].append(other)
            else:
                scores[UNCATALOGUED].append(max(own, other))
            report.add(
                kind,
                CONTROL,
                index,
                round(elapsed, DIGITS),
                cancion=copied.track_id if kind in COPY_KINDS else host.track_id,
                puntaje_copiada=round(own, DIGITS),
                puntaje_otra=round(other, DIGITS),
                desfase_esperado=upload.copies[0][1] if upload.copies else None,
                desfase=offset,
                detectada=found_copies(detection, upload) == len(upload.copies) > 0,
                falsa=_false_alarm(detection, copied.track_id if kind in COPY_KINDS else None),
            )
    return scores


def add_score_summary(report: Report, scores: dict[str, list[float]], catalog_songs: int) -> None:
    for kind, values in scores.items():
        for name, value in zip(
            (MINIMUM, MEDIAN, MAXIMUM),
            (min(values), statistics.median(values), max(values)),
            strict=True,
        ):
            report.add(name, kind, catalog_songs, 0.0, puntaje=round(value, DIGITS))


def run(arguments: argparse.Namespace) -> Report:
    root = dataset_directory(arguments.data_dir.resolve())
    catalog = read_catalog(root / "catalog.csv")
    shuffled = random.Random(arguments.seed).sample(catalog, len(catalog))
    held_out = eligible_songs(shuffled[: len(shuffled) // 2], root, arguments.held_out)
    catalogued = eligible_songs(shuffled[len(shuffled) // 2 :], root, _catalogued_needed(arguments))
    settings = service_settings(SERVICE_URL)
    report = Report(
        name=REPORT_NAME,
        parameters={
            "durations": list(arguments.durations),
            "repetitions": arguments.repetitions,
            "cases": arguments.cases,
            "held_out": arguments.held_out,
            "seed": arguments.seed,
            "catalog_songs": len(catalog) - arguments.held_out,
            "window_seconds": WINDOWING.window_seconds,
            "hop_seconds": WINDOWING.hop_seconds,
            "neighbours_per_window": settings.neighbours_per_window,
            "min_run_windows": settings.min_run_windows,
            "match_threshold": settings.match_threshold,
            "upload_format": UPLOAD_FORMAT,
            "machine": machine_description(),
        },
    )
    tracks = Tracks(root)
    with engine_copy(arguments.engine_dir, arguments.workspace, arguments.data_dir) as url:
        hold_out(url, held_out)
        with service(url) as client:
            detect(client, whole(tracks, catalogued[-1], catalogued=True))
            scores = measure_controls(report, client, tracks, catalogued, held_out, arguments.cases)
            add_score_summary(report, scores, report.parameters["catalog_songs"])
            measure_latency(
                report,
                client,
                tracks,
                catalogued[arguments.cases :],
                held_out[arguments.cases :],
                arguments,
            )
    return report


def _catalogued_needed(arguments: argparse.Namespace) -> int:
    """Pistas del catálogo que usan los casos, las subidas pegadas y el calentamiento."""
    per_upload = sum((_clips(seconds) + 1) // 2 for seconds in arguments.durations)
    return int(arguments.cases) + int(arguments.repetitions) * per_upload + 1


def _scores(detection: dict[str, Any], copied: int) -> tuple[float, float, float | None]:
    """El mejor puntaje de la canción copiada, el mejor de cualquier otra y el desfase de la
    mejor coincidencia de la copiada."""
    own = [match for match in detection["matches"] if match["song"]["id"] == copied]
    other = [match["score"] for match in detection["matches"] if match["song"]["id"] != copied]
    best = own[0] if own else None
    return (
        best["score"] if best else 0.0,
        max(other, default=0.0),
        best["offset_seconds"] if best else None,
    )


def _false_alarm(detection: dict[str, Any], copied: int | None) -> bool:
    return any(
        match["above_threshold"] and match["song"]["id"] != copied for match in detection["matches"]
    )


def _server_timing(header: str) -> dict[str, float]:
    stages = {}
    for part in header.split(","):
        name, _, duration = part.strip().partition(";dur=")
        stages[name] = float(duration)
    return stages


def _stereo(samples: np.ndarray) -> np.ndarray:
    if samples.shape[1] == UPLOAD_CHANNELS:
        return samples
    return np.repeat(samples[:, :1], UPLOAD_CHANNELS, axis=1)


def _clips(seconds: int) -> int:
    return math.ceil(seconds / WHOLE_SECONDS)


def _frames(seconds: float) -> int:
    return round(seconds * UPLOAD_RATE)


def _cycle(songs: Sequence[CatalogSong]) -> Iterator[CatalogSong]:
    while True:
        yield from songs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine-dir", type=Path, required=True, help="gestor con el catálogo")
    parser.add_argument("--data-dir", type=Path, required=True, help="carpeta de datasets")
    parser.add_argument("--durations", type=int, nargs="+", default=DEFAULT_DURATIONS)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--cases", type=int, default=DEFAULT_CASES)
    parser.add_argument("--held-out", type=int, default=DEFAULT_HELD_OUT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--workspace", type=Path, default=None, help="dónde copiar el gestor (temporal)"
    )
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    report = run(arguments)
    print(report.render())
    print(f"\nResultados crudos en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
