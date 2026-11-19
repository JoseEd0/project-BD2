"""La evaluación de la detección de copyright (issue #77): remixes con la verdad conocida.

    uv run --no-sync python -m benchmarks.copyright_evaluation_benchmark \\
        --engine-dir data/copyright-engine --data-dir data/datasets

El gestor es **una copia** del que dejó el trabajo de carga con el catálogo entero, servida
en otro proceso y en un puerto libre, como en el §14; de la copia se borran los tramos de
`--held-out` canciones al azar, que pasan a ser pistas **no catalogadas**.

1. **Remixes.** Con `--seed`, `--positives` remixes de 1 a 3 fragmentos de 5 a 20 s de
   canciones del catálogo, pegados con fundidos de 1 s, y `--negatives` remixes iguales con
   canciones no catalogadas. La verdad de cada uno dice qué canción suena, dónde en el remix y
   de dónde sale del original.
2. **Distorsiones**, cada una por separado sobre el mismo remix: ruido a 20 y 10 dB de SNR,
   tempo ±5 %, tono ±1 semitono, MP3 a 64 kbit/s, pasa bajos a 4 kHz y volumen -12 dB. Cada
   versión se sube en FLAC (sin pérdida) a `POST /detections` del servicio, con su
   configuración por omisión.
3. **Métricas.** Curva ROC de todas las versiones juntas y el umbral de máxima J de Youden;
   a ese umbral, precisión, recall y F1 por distorsión, IoU de los tramos localizados y
   recall por largo del fragmento; latencia por minuto de audio. Además, el área bajo la
   curva de otras definiciones del puntaje sobre las mismas coincidencias.

Las detecciones crudas quedan en `copyright_evaluacion_casos.json`; con `--reuse-cases` las
cifras se recalculan desde ese archivo sin el gestor.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import soundfile

from benchmarks.common import Report, load_average, machine_description, results_directory
from benchmarks.copyright_detection_benchmark import (
    SERVICE_URL,
    eligible_songs,
    engine_copy,
    hold_out,
    service,
    service_settings,
)
from benchmarks.remixes.distortions import (
    Distortion,
    low_pass,
    mp3,
    noise,
    pitch,
    speed,
    unchanged,
    volume,
)
from benchmarks.remixes.evaluation import (
    COVERAGE_KEY,
    SCORE_KEY,
    SIMILARITY_KEY,
    Scoring,
    Upload,
    add_scoring_comparison,
    analyse,
    compact_match,
    load_uploads,
    match_of,
    save_uploads,
)
from benchmarks.remixes.generator import RemixPlan, RemixShape, plan_remix, render
from benchmarks.remixes.metrics import Case, Truth
from query.plan import MILLISECONDS_PER_SECOND
from songcheck.catalog import CatalogSong, dataset_directory, read_catalog
from songcheck.detection_api import DETECTIONS_PATH
from songcheck.windows import WINDOWING

REPORT_NAME = "copyright_evaluacion"
CASES_NAME = "copyright_evaluacion_casos"
SAMPLE_RATE = 44_100
UPLOAD_FORMAT = "FLAC"
UPLOAD_NAME = "remix.flac"
DEFAULT_POSITIVES = 200
DEFAULT_NEGATIVES = 200
DEFAULT_HELD_OUT = 200
DEFAULT_SEED = 20261121
DEFAULT_MIN_FRAGMENTS = 1
DEFAULT_MAX_FRAGMENTS = 3
DEFAULT_MIN_FRAGMENT_SECONDS = 5.0
DEFAULT_MAX_FRAGMENT_SECONDS = 20.0
DEFAULT_CROSSFADE_SECONDS = 1.0
DEFAULT_THRESHOLD_STEP = 0.01
# 7,5 s es la copia más corta que llena dos ventanas seguidas del catálogo (ventana + salto).
DEFAULT_LENGTH_BINS = (5.0, 7.5, 10.0, 15.0, 20.0)
DEFAULT_LOAD_SAMPLES = 10
# Las pistas de fma_small duran de 29,96 a 30,00 s decodificadas.
SOURCE_SECONDS = 29.9
NOISE_30_DB = 30.0
NOISE_20_DB = 20.0
NOISE_10_DB = 10.0
FASTER = 1.05
SLOWER = 0.95
SEMITONE_UP = 1.0
SEMITONE_DOWN = -1.0
MP3_KBPS = 64.0
LOW_PASS_HZ = 4_000.0
SLIGHTLY_QUIETER_DB = -1.0
QUIETER_DB = -3.0
MUCH_QUIETER_DB = -12.0
DISTORTIONS = (
    unchanged(),
    noise("ruido 30 dB", NOISE_30_DB),
    noise("ruido 20 dB", NOISE_20_DB),
    noise("ruido 10 dB", NOISE_10_DB),
    speed("tempo +5 %", FASTER),
    speed("tempo -5 %", SLOWER),
    pitch("tono +1", SEMITONE_UP),
    pitch("tono -1", SEMITONE_DOWN),
    mp3("MP3 64 kbps", MP3_KBPS),
    low_pass("pasa bajos 4 kHz", LOW_PASS_HZ),
    volume("volumen -1 dB", SLIGHTLY_QUIETER_DB),
    volume("volumen -3 dB", QUIETER_DB),
    volume("volumen -12 dB", MUCH_QUIETER_DB),
)
# El puntaje del servicio frente a otras definiciones, sobre las mismas coincidencias: el de
# #76 (similitud media · cobertura), que la evaluación de #77 reemplazó, y la similitud sola.
SCORINGS = (
    Scoring("evidencia de la racha (servicio)", lambda match: float(match[SCORE_KEY])),
    Scoring(
        "similitud · cobertura (#76)",
        lambda match: float(match[SIMILARITY_KEY]) * float(match[COVERAGE_KEY]),
    ),
    Scoring("similitud media", lambda match: float(match[SIMILARITY_KEY])),
)


MEASURED_PARAMETERS = ("machine", "catalog_songs", "load_average")


class Library:
    """El audio de las pistas, mezclado a mono y a `SAMPLE_RATE`, leído cuando se pide."""

    def __init__(self, root: Path, songs: Sequence[CatalogSong]) -> None:
        self._root = root
        self._paths = {song.track_id: song.path for song in songs}

    def mono(self, song_id: int) -> np.ndarray:
        data, rate = soundfile.read(
            self._root / self._paths[song_id], dtype="float32", always_2d=True
        )
        if rate != SAMPLE_RATE:
            raise ValueError(f"la pista {song_id} está a {rate} Hz y no a {SAMPLE_RATE}")
        return np.asarray(data.mean(axis=1), dtype=np.float32)


def remix_shape(arguments: argparse.Namespace) -> RemixShape:
    return RemixShape(
        min_fragments=arguments.min_fragments,
        max_fragments=arguments.max_fragments,
        min_fragment_seconds=arguments.min_fragment_seconds,
        max_fragment_seconds=arguments.max_fragment_seconds,
        crossfade_seconds=arguments.crossfade_seconds,
        source_seconds=SOURCE_SECONDS,
    )


def plan_all(
    rng: random.Random,
    catalogued: Sequence[int],
    uncatalogued: Sequence[int],
    shape: RemixShape,
    arguments: argparse.Namespace,
) -> list[RemixPlan]:
    """Los remixes con canciones del catálogo, cada uno con canciones que no repite ningún
    otro, y después los de canciones no catalogadas, que sí se repiten entre remixes."""
    width = shape.max_fragments
    positives = [
        plan_remix(rng, catalogued[index * width : (index + 1) * width], shape, catalogued=True)
        for index in range(arguments.positives)
    ]
    negatives = [
        plan_remix(rng, uncatalogued, shape, catalogued=False) for _ in range(arguments.negatives)
    ]
    return positives + negatives


def truth_of(plan: RemixPlan, distortion: Distortion) -> tuple[Truth, ...]:
    if not plan.catalogued:
        return ()
    return tuple(
        Truth(
            fragment.song_id,
            fragment.upload.scaled(distortion.time_scale),
            fragment.original,
        )
        for fragment in plan.fragments
    )


def flac(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, SAMPLE_RATE, format=UPLOAD_FORMAT)
    return buffer.getvalue()


def measure(
    arguments: argparse.Namespace, report: Report
) -> tuple[list[Upload], list[list[dict[str, Any]]]]:
    root = dataset_directory(arguments.data_dir.resolve())
    catalog = read_catalog(root / "catalog.csv")
    shuffled = random.Random(arguments.seed).sample(catalog, len(catalog))
    held_out = eligible_songs(shuffled[: len(shuffled) // 2], root, arguments.held_out)
    shape = remix_shape(arguments)
    catalogued = eligible_songs(
        shuffled[len(shuffled) // 2 :], root, arguments.positives * shape.max_fragments
    )
    plans = plan_all(
        random.Random(arguments.seed),
        [song.track_id for song in catalogued],
        [song.track_id for song in held_out],
        shape,
        arguments,
    )
    library = Library(root, [*catalogued, *held_out])
    report.parameters["catalog_songs"] = len(catalog) - len(held_out)
    uploads: list[Upload] = []
    matches: list[list[dict[str, Any]]] = []
    loads: list[float] = []
    sample_every = max(1, len(plans) // arguments.load_samples)
    with engine_copy(arguments.engine_dir, arguments.workspace, arguments.data_dir) as url:
        hold_out(url, held_out)
        with service(url) as client:
            warm_up = render(plans[0], library.mono, SAMPLE_RATE, shape.crossfade_seconds)
            client.post(DETECTIONS_PATH, files={"file": (UPLOAD_NAME, flac(warm_up))})
            for index, plan in enumerate(plans):
                if index % sample_every == 0:
                    loads.append(load_average())
                clean = render(plan, library.mono, SAMPLE_RATE, shape.crossfade_seconds)
                for position, distortion in enumerate(DISTORTIONS):
                    rng = np.random.default_rng((arguments.seed, index, position))
                    samples = distortion.apply(clean, SAMPLE_RATE, rng)
                    started = time.perf_counter()
                    response = client.post(
                        DETECTIONS_PATH, files={"file": (UPLOAD_NAME, flac(samples))}
                    )
                    elapsed = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
                    response.raise_for_status()
                    found = [compact_match(match) for match in response.json()["matches"]]
                    uploads.append(_upload(index, plan, distortion, samples, elapsed, found))
                    matches.append(found)
    loads.append(load_average())
    report.parameters["load_average"] = loads
    return uploads, matches


def run(arguments: argparse.Namespace) -> Report:
    settings = service_settings(SERVICE_URL)
    report = Report(
        name=REPORT_NAME,
        parameters={
            "positives": arguments.positives,
            "negatives": arguments.negatives,
            "held_out": arguments.held_out,
            "seed": arguments.seed,
            "fragments": [arguments.min_fragments, arguments.max_fragments],
            "fragment_seconds": [arguments.min_fragment_seconds, arguments.max_fragment_seconds],
            "crossfade_seconds": arguments.crossfade_seconds,
            "sample_rate": SAMPLE_RATE,
            "upload_format": UPLOAD_FORMAT,
            "distortions": [distortion.name for distortion in DISTORTIONS],
            "window_seconds": WINDOWING.window_seconds,
            "hop_seconds": WINDOWING.hop_seconds,
            "neighbours_per_window": settings.neighbours_per_window,
            "min_run_windows": settings.min_run_windows,
            "threshold_step": arguments.threshold_step,
            "length_bins": list(arguments.length_bins),
            "machine": machine_description(),
        },
    )
    cases_path = arguments.output / f"{CASES_NAME}.json"
    if arguments.reuse_cases:
        uploads, matches = load_uploads(cases_path)
        report.parameters.update(_measured_parameters(arguments.output / f"{REPORT_NAME}.json"))
    else:
        uploads, matches = measure(arguments, report)
        save_uploads(cases_path, uploads, matches)
    thresholds = _thresholds(arguments.threshold_step)
    analyse(
        report,
        uploads,
        thresholds,
        arguments.length_bins,
        DISTORTIONS[0].name,
    )
    add_scoring_comparison(report, uploads, matches, SCORINGS, thresholds)
    return report


def _upload(
    index: int,
    plan: RemixPlan,
    distortion: Distortion,
    samples: np.ndarray,
    elapsed: float,
    found: list[dict[str, Any]],
) -> Upload:
    return Upload(
        index,
        distortion.name,
        len(samples) / SAMPLE_RATE,
        elapsed,
        Case(truth_of(plan, distortion), tuple(match_of(match) for match in found)),
        tuple(fragment.upload.seconds for fragment in plan.fragments) if plan.catalogued else (),
    )


def _measured_parameters(path: Path) -> dict[str, Any]:
    """Lo que se anotó al medir —el equipo, su carga y el tamaño del catálogo— y no se puede
    volver a saber sin medir."""
    previous = json.loads(path.read_text(encoding="utf-8"))["parameters"]
    return {key: previous[key] for key in MEASURED_PARAMETERS if key in previous}


def _thresholds(step: float) -> list[float]:
    count = round(1 / step)
    return [round(index * step, 6) for index in range(count + 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine-dir", type=Path, help="gestor con el catálogo")
    parser.add_argument("--data-dir", type=Path, help="carpeta de datasets")
    parser.add_argument("--positives", type=int, default=DEFAULT_POSITIVES)
    parser.add_argument("--negatives", type=int, default=DEFAULT_NEGATIVES)
    parser.add_argument("--held-out", type=int, default=DEFAULT_HELD_OUT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--min-fragments", type=int, default=DEFAULT_MIN_FRAGMENTS)
    parser.add_argument("--max-fragments", type=int, default=DEFAULT_MAX_FRAGMENTS)
    parser.add_argument("--min-fragment-seconds", type=float, default=DEFAULT_MIN_FRAGMENT_SECONDS)
    parser.add_argument("--max-fragment-seconds", type=float, default=DEFAULT_MAX_FRAGMENT_SECONDS)
    parser.add_argument("--crossfade-seconds", type=float, default=DEFAULT_CROSSFADE_SECONDS)
    parser.add_argument("--threshold-step", type=float, default=DEFAULT_THRESHOLD_STEP)
    parser.add_argument("--length-bins", type=float, nargs="+", default=DEFAULT_LENGTH_BINS)
    parser.add_argument("--load-samples", type=int, default=DEFAULT_LOAD_SAMPLES)
    parser.add_argument(
        "--reuse-cases", action="store_true", help="recalcula desde el JSON de casos, sin gestor"
    )
    parser.add_argument(
        "--workspace", type=Path, default=None, help="dónde copiar el gestor (temporal)"
    )
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    if not arguments.reuse_cases and (arguments.engine_dir is None or arguments.data_dir is None):
        parser.error("sin --reuse-cases hacen falta --engine-dir y --data-dir")
    report = run(arguments)
    print(report.render())
    print(f"\nResultados crudos en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
