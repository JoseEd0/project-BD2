"""Rostros: milisegundos de cada etapa del pipeline por foto y cobertura de YuNet en LFW.

    .venv/bin/python -m faces lfw --salida data/datasets/lfw
    .venv/bin/python -m faces models --salida data/models/faces
    .venv/bin/python -m benchmarks.faces_benchmark --lfw data/datasets/lfw/lfw \\
        --models data/models/faces --sides 500 1000 2000 4000 --sample 500 --threads 1

Se mide cada foto una vez, por etapa, con el mismo código que usará el servicio:

- **decodificar**: de los bytes JPEG a BGR, con la orientación EXIF y la conversión de canales;
- **detectar**: llevar la imagen al lado de detección y correr YuNet;
- **embeber**: alinear el rostro de mayor área con sus cinco puntos y correr SFace;
  solo en las fotos con rostro, porque en las demás no se hace.

Todas las 13 233 fotos de LFW se miden en su tamaño original (250 x 250 px): de ahí salen
la cobertura del detector y la latencia de una foto chica. Para ver qué pasa con fotos de
celular, una muestra al azar se amplía a cada lado de `--sides` y se vuelve a codificar en
JPEG antes de medir; eso no entra en el tiempo. Las primeras `--warmup` fotos de cada
tamaño se procesan sin medir, para que la primera inferencia no pague la preparación de
las redes.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

import cv2
import numpy as np
import PIL

from benchmarks.common import Report, machine_description, results_directory
from faces import FacePipeline, PipelineConfig, locate_models
from faces.lfw import lfw_images
from query.plan import MILLISECONDS_PER_SECOND

REPORT_NAME = "rostros"
LATENCY = "latencia"
COVERAGE = "cobertura en LFW"
DETECTOR = "YuNet"
DECODE_STAGE = "decodificar"
DETECT_STAGE = "detectar (YuNet)"
EMBED_STAGE = "alinear y embeber (SFace)"
TOTAL = "total"
LFW_SIDE = 250
PERCENTILES = (50, 95)
DEFAULT_SIDES = (500, 1000, 2000, 4000)
DEFAULT_SAMPLE = 500
DEFAULT_WARMUP = 20
DEFAULT_SEED = 20261118
DEFAULT_THREADS = 1
JPEG_QUALITY = 92
DIGITS = 3

Result = TypeVar("Result")


@dataclass(frozen=True, slots=True)
class ImageTiming:
    """Lo que tardó cada etapa con una foto, en milisegundos, y cuántos rostros tenía."""

    decode: float
    detect: float
    embed: float | None
    faces: int


def timed(action: Callable[[], Result]) -> tuple[Result, float]:
    started = time.perf_counter()
    result = action()
    return result, (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def measure_photo(pipeline: FacePipeline, data: bytes) -> ImageTiming:
    image, decode = timed(lambda: pipeline.decode(data))
    faces, detect = timed(lambda: pipeline.detect(image))
    if not faces:
        return ImageTiming(decode, detect, None, 0)
    _, embed = timed(lambda: pipeline.embed(image, faces[0]))
    return ImageTiming(decode, detect, embed, len(faces))


def measure_all(
    pipeline: FacePipeline, paths: Sequence[Path], photo: Callable[[Path], bytes], warmup: int
) -> list[ImageTiming]:
    """Cada foto de `paths`, leída con `photo` justo antes de medirla: así nunca están
    todas en memoria, que ampliadas a 4 000 px pasarían de un gigabyte."""
    for path in paths[:warmup]:
        measure_photo(pipeline, photo(path))
    return [measure_photo(pipeline, photo(path)) for path in paths]


def enlarged(path: Path, side: int) -> bytes:
    """La foto de `path` ampliada a `side` px de lado mayor y codificada en JPEG."""
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(f"no se pudo leer {path}")
    height, width = image.shape[:2]
    scale = side / max(height, width)
    size = (round(width * scale), round(height * scale))
    resized = cv2.resize(image, size, interpolation=cv2.INTER_CUBIC)
    _, buffer = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return bytes(buffer)


def add_latencies(report: Report, side: int, timings: Sequence[ImageTiming]) -> None:
    with_face = [timing for timing in timings if timing.embed is not None]
    stages = {
        DECODE_STAGE: [timing.decode for timing in timings],
        DETECT_STAGE: [timing.detect for timing in timings],
        EMBED_STAGE: [timing.embed for timing in with_face if timing.embed is not None],
        TOTAL: [timing.decode + timing.detect + (timing.embed or 0.0) for timing in timings],
    }
    for stage, values in stages.items():
        median, high = (round(float(value), DIGITS) for value in np.percentile(values, PERCENTILES))
        report.add(
            stage,
            LATENCY,
            side,
            median,
            p95_ms=high,
            media_ms=round(statistics.fmean(values), DIGITS),
            fotos=len(values),
            con_rostro=len(with_face),
        )


def add_coverage(report: Report, timings: Sequence[ImageTiming]) -> None:
    with_face = sum(1 for timing in timings if timing.faces)
    several = sum(1 for timing in timings if timing.faces > 1)
    report.add(
        DETECTOR,
        COVERAGE,
        len(timings),
        round(sum(timing.detect for timing in timings), DIGITS),
        con_rostro=with_face,
        sin_rostro=len(timings) - with_face,
        con_varios=several,
        cobertura=round(with_face / len(timings), 5),
    )


def run(arguments: argparse.Namespace) -> Report:
    cv2.setNumThreads(arguments.threads)
    config = PipelineConfig()
    pipeline = FacePipeline(locate_models(arguments.models), config)
    paths = [image.path for image in lfw_images(arguments.lfw)]
    report = Report(REPORT_NAME, _parameters(arguments, config, len(paths)))
    native = measure_all(pipeline, paths, Path.read_bytes, arguments.warmup)
    add_coverage(report, native)
    add_latencies(report, LFW_SIDE, native)
    sample = random.Random(arguments.seed).sample(paths, min(arguments.sample, len(paths)))
    for side in arguments.sides:
        timings = measure_all(pipeline, sample, partial(enlarged, side=side), arguments.warmup)
        add_latencies(report, side, timings)
        print(f"medido el lado {side} px")
    return report


def _parameters(
    arguments: argparse.Namespace, config: PipelineConfig, images: int
) -> dict[str, Any]:
    return {
        "fotos_lfw": images,
        "lado_lfw": LFW_SIDE,
        "lados": list(arguments.sides),
        "muestra": arguments.sample,
        "calentamiento": arguments.warmup,
        "semilla": arguments.seed,
        "hilos_opencv": arguments.threads,
        "calidad_jpeg": JPEG_QUALITY,
        "pipeline": asdict(config),
        "opencv": cv2.__version__,
        "pillow": PIL.__version__,
        "equipo": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lfw", type=Path, required=True, help="carpeta lfw/ con las fotos")
    parser.add_argument("--models", type=Path, required=True, help="carpeta de YuNet y SFace")
    parser.add_argument("--sides", type=int, nargs="+", default=list(DEFAULT_SIDES))
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    report = run(arguments)
    print(report.render())
    print(f"\nResultados crudos en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
