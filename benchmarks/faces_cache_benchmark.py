"""Reconocimiento en vivo: cuánto acierta la caché de rastros y cuántos milisegundos ahorra
según lo rápido que se mueve el rostro, a lo largo de una secuencia de video sintética.

    .venv/bin/python -m benchmarks.faces_cache_benchmark \\
        --api-url http://127.0.0.1:<web>/api --url ws://127.0.0.1:<web>/api/ws/recognize \\
        --speeds 0 16 32 40 48 56 64 96 --frames 200 --warmup 20 --fps 10 \\
        --compose-project rostros-bench

Contra el mismo despliegue que `faces_stream_benchmark` (ver su docstring). La secuencia es
el retrato de prueba en un cuadro de 1 280 x 720 que cruza de izquierda a derecha a `v`
píxeles por cuadro y, al llegar al borde, vuelve a entrar por la izquierda: con cada `v` de
`--speeds` se mide una pasada con la caché de rastros y otra sin ella, con el mismo cliente
que espera cada respuesta. Un rostro sale «de la caché» si su rastro tenía una identidad
vigente: el rastro sigue al rostro mientras su caja se solape con la del cuadro anterior
(IoU de `FACES_TRACK_MIN_IOU`), así que la tasa de aciertos cae cuando el salto entre
cuadros se come ese solapamiento.

El cuadro es ancho a propósito: un rastro perdido se recuerda `FACES_TRACK_LOST_SECONDS`, y
si el rostro volviera antes a un lugar por el que pasó —rebotando en el borde de un cuadro
angosto— el rastro viejo lo recogería y la tasa no diría nada de la velocidad. Con 1 021 px
de recorrido, una vuelta dura más de un segundo hasta los 96 px por cuadro.

De cada pasada con caché se guarda además la tasa de aciertos de cada segundo de video: cada
`FACES_TRACK_VERIFY_SECONDS` el rastro se vuelve a verificar y ese cuadro no acierta.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from apps.faces.tests.faces_support import SINGLE_PORTRAIT, selfie
from PIL import Image

from benchmarks.common import Report, machine_description, results_directory
from benchmarks.faces_stream_benchmark import (
    BACKGROUND_GRAY,
    JPEG_QUALITY,
    WITH_CACHE,
    WITHOUT_CACHE,
    Run,
    docker_load_average,
    enroll,
    measure,
)

REPORT_NAME = "rostros_cache"
SPEED = "por velocidad"
TIMELINE = "aciertos por segundo"
TECHNIQUE = "{speed} px por cuadro"
DEFAULT_SPEEDS = (0, 16, 32, 40, 48, 56, 64, 96)
# Un cuadro de cámara de 720p y un rostro de 87 px de ancho, por encima de los 80 px que pide
# el reconocimiento: el IoU con el cuadro anterior baja de 0,3 desde unos 47 px de salto.
FRAME_SIZE = (1280, 720)
FACE_HEIGHT = 300
DEFAULT_FRAMES = 200
DEFAULT_WARMUP = 20
DEFAULT_FPS = 10.0
DIGITS = 3
FRACTION_DIGITS = 4


def sliding_frames(portrait: Image.Image, count: int, speed: int) -> list[bytes]:
    """`count` cuadros JPEG con el rostro corrido `speed` píxeles por cuadro hacia la
    derecha, que vuelve a entrar por la izquierda al pasar el borde; centrado en vertical."""
    width = round(portrait.width * FACE_HEIGHT / portrait.height)
    face = portrait.resize((width, FACE_HEIGHT), Image.Resampling.LANCZOS)
    top = (FRAME_SIZE[1] - FACE_HEIGHT) // 2
    room = FRAME_SIZE[0] - width
    return [_jpeg(face, (number * speed) % (room + 1), top) for number in range(count)]


def _jpeg(face: Image.Image, left: int, top: int) -> bytes:
    canvas = Image.new("RGB", FRAME_SIZE, (BACKGROUND_GRAY,) * 3)
    canvas.paste(face, (left, top))
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def hit_rate_per_second(cached_per_frame: Sequence[int], fps: float) -> list[float]:
    """La fracción de cuadros con su rostro de la caché en cada segundo de video completo."""
    window = max(1, round(fps))
    seconds = len(cached_per_frame) // window
    return [
        round(sum(cached_per_frame[s * window : (s + 1) * window]) / window, FRACTION_DIGITS)
        for s in range(seconds)
    ]


def add_speed(
    report: Report,
    speed: int,
    runs: dict[str, Run],
    loads: dict[str, tuple[float, float | None]],
    fps: float,
) -> None:
    summaries = {operation: run.summary() for operation, run in runs.items()}
    saved_p50 = summaries[WITHOUT_CACHE]["p50_ms"] - summaries[WITH_CACHE]["p50_ms"]
    saved_mean = _mean(runs[WITHOUT_CACHE].latencies_ms) - _mean(runs[WITH_CACHE].latencies_ms)
    for operation, summary in summaries.items():
        faces = max(1, summary["rostros"])
        extra: dict[str, Any] = {
            "velocidad_px": speed,
            "tasa_de_aciertos": round(summary["de_la_cache"] / faces, FRACTION_DIGITS),
            "carga_equipo": round(loads[operation][0], 2),
            "carga_docker": loads[operation][1],
            **summary,
        }
        if operation == WITH_CACHE:
            extra["ahorro_p50_ms"] = round(saved_p50, DIGITS)
            extra["ahorro_medio_ms"] = round(saved_mean, DIGITS)
        report.add(operation, SPEED, speed, summary["p50_ms"], **extra)
    cached = runs[WITH_CACHE].cached_per_frame
    rates = hit_rate_per_second(cached, fps)
    report.add(
        TECHNIQUE.format(speed=speed),
        TIMELINE,
        len(cached),
        0.0,
        segundo=list(range(1, len(rates) + 1)),
        tasa_de_aciertos=rates,
    )


def run_benchmark(arguments: argparse.Namespace) -> Report:
    portrait = selfie(SINGLE_PORTRAIT)
    enroll(arguments.api_url)
    report = Report(
        REPORT_NAME,
        {
            "url": arguments.url,
            "velocidades_px_por_cuadro": list(arguments.speeds),
            "cuadros": arguments.frames,
            "calentamiento": arguments.warmup,
            "fps": arguments.fps,
            "cuadro": f"{FRAME_SIZE[0]}x{FRAME_SIZE[1]} JPEG q{JPEG_QUALITY}",
            "equipo": machine_description(),
        },
    )
    total = arguments.warmup + arguments.frames + 1
    for speed in arguments.speeds:
        images = sliding_frames(portrait, total, speed)
        runs: dict[str, Run] = {}
        loads: dict[str, tuple[float, float | None]] = {}
        for use_cache, operation in ((True, WITH_CACHE), (False, WITHOUT_CACHE)):
            loads[operation] = (
                os.getloadavg()[0],
                docker_load_average(arguments.compose_project),
            )
            runs[operation] = asyncio.run(
                measure(
                    arguments.url,
                    use_cache,
                    images,
                    arguments.warmup,
                    arguments.frames,
                    arguments.fps,
                )
            )
        add_speed(report, speed, runs, loads, arguments.fps)
        print(f"velocidad {speed} px por cuadro medida")
    return report


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--api-url", required=True, help="el API por HTTP, para registrar")
    parser.add_argument("--url", required=True, help="el WebSocket del reconocimiento en vivo")
    parser.add_argument("--speeds", type=int, nargs="+", default=list(DEFAULT_SPEEDS))
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    parser.add_argument(
        "--compose-project", help="proyecto de compose del que leer la carga de Docker"
    )
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    arguments = parse_arguments(argv)
    report = run_benchmark(arguments)
    path = report.save(results_directory(arguments.output))
    print(report.render())
    print(f"\nresultados en {path}")


if __name__ == "__main__":
    main()
