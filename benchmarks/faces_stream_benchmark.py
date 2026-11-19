"""Reconocimiento en vivo: latencia de punta a punta por cuadro, con y sin la caché de rastros,
a través del nginx de la web y directo a la API.

    docker compose -p rostros-bench -f compose.yaml -f benchmarks/faces_stream.compose.yaml \\
        up -d --wait faces-web
    .venv/bin/python -m benchmarks.faces_stream_benchmark \\
        --api-url http://127.0.0.1:<web>/api \\
        --target nginx=ws://127.0.0.1:<web>/api/ws/recognize \\
        --target directo=ws://127.0.0.1:<api>/ws/recognize \\
        --frames 300 --warmup 20 --fps 10 --compose-project rostros-bench

El cliente es el celular: manda un cuadro JPEG de 640 x 480 con el rostro de una persona
registrada, que se mueve unos píxeles de un cuadro a otro, espera su respuesta y manda el
siguiente al ritmo de `--fps`. La latencia de un cuadro va de que el cliente lo envía a que
recibe su resultado; el servidor devuelve además lo que tardó cada etapa. Cada destino se
mide con la caché de rastros (Redis) y sin ella (`hello` con `track_cache: false`), en la
misma conexión de prueba, y se anota la carga media del equipo antes y después.

La persona se registra al empezar por `--api-url` con dos tomas de frente: el despliegue
del benchmark pide el plan `["front", "front"]` (ver `faces_stream.compose.yaml`).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import random
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from apps.faces.tests.faces_support import OTHER_PORTRAIT, SINGLE_PORTRAIT, selfie
from PIL import Image
from websockets.asyncio.client import ClientConnection, connect

from benchmarks.common import Report, machine_description, results_directory
from faces.api.realtime.protocol import encode_frame

REPORT_NAME = "rostros_tiempo_real"
NAME = "Eileen Collins"
WITH_CACHE = "con caché de rastros"
WITHOUT_CACHE = "sin caché de rastros"
STAGES = ("decode", "detect", "track", "embed", "search")
REST_STAGE = "resto"
TAIL_FIELD = "p95_sobre_p50_ms"
SERVER_TAIL_FIELD = "servidor_p95_sobre_p50_ms"
PERCENTILES = (50, 95)
FRAME_SIZE = (640, 480)
FACE_HEIGHT = 400
BACKGROUND_GRAY = 96
JPEG_QUALITY = 80
JITTER_PIXELS = 12
DISTINCT_FRAMES = 30
DEFAULT_FRAMES = 300
DEFAULT_WARMUP = 20
DEFAULT_FPS = 10.0
DEFAULT_SEED = 20261119
TARGET_SEPARATOR = "="
LOADAVG_COMMAND = ("cat", "/proc/loadavg")
ALREADY_ENROLLED = "AlreadyEnrolledError"
DIGITS = 3
MILLISECONDS_PER_SECOND = 1000.0


@dataclass
class Run:
    """Lo medido en una pasada: la latencia de cada cuadro y lo que dijo el servidor."""

    latencies_ms: list[float] = field(default_factory=list)
    stages: dict[str, list[float]] = field(default_factory=lambda: {s: [] for s in STAGES})
    rest_ms: list[float] = field(default_factory=list)
    server_total_ms: list[float] = field(default_factory=list)
    cached: int = 0
    known: int = 0
    faces: int = 0
    dropped: int = 0

    def add(self, latency_ms: float, result: dict[str, Any]) -> None:
        timings = result["timings"]
        self.latencies_ms.append(latency_ms)
        for stage in STAGES:
            self.stages[stage].append(float(timings[stage]))
        self.rest_ms.append(latency_ms - sum(float(timings[stage]) for stage in STAGES))
        self.server_total_ms.append(float(timings["total"]))
        self.faces += len(result["faces"])
        self.cached += sum(face["cached"] for face in result["faces"])
        self.known += sum(face["status"] == "known" for face in result["faces"])
        self.dropped = int(result["dropped"])

    def summary(self) -> dict[str, Any]:
        figures: dict[str, Any] = {
            f"p{percentile}_ms": _percentile(self.latencies_ms, percentile)
            for percentile in PERCENTILES
        }
        for stage, values in [*self.stages.items(), (REST_STAGE, self.rest_ms)]:
            for percentile in PERCENTILES:
                figures[f"{stage}_p{percentile}_ms"] = _percentile(values, percentile)
        for percentile in PERCENTILES:
            figures[f"servidor_p{percentile}_ms"] = _percentile(self.server_total_ms, percentile)
        figures[TAIL_FIELD] = round(figures["p95_ms"] - figures["p50_ms"], DIGITS)
        figures[SERVER_TAIL_FIELD] = round(
            figures["servidor_p95_ms"] - figures["servidor_p50_ms"], DIGITS
        )
        figures["max_ms"] = round(max(self.latencies_ms), DIGITS)
        figures["rostros"] = self.faces
        figures["de_la_cache"] = self.cached
        figures["reconocidos"] = self.known
        figures["descartados"] = self.dropped
        figures["latencias_ms"] = [round(value, DIGITS) for value in self.latencies_ms]
        return figures


def frames(portrait: Image.Image, count: int, seed: int) -> list[bytes]:
    """`count` cuadros JPEG con el rostro corrido unos píxeles al azar en cada uno."""
    jitter = random.Random(seed)
    width = round(portrait.width * FACE_HEIGHT / portrait.height)
    face = portrait.resize((width, FACE_HEIGHT), Image.Resampling.LANCZOS)
    left = (FRAME_SIZE[0] - width) // 2
    top = (FRAME_SIZE[1] - FACE_HEIGHT) // 2
    encoded = []
    for _ in range(count):
        canvas = Image.new("RGB", FRAME_SIZE, (BACKGROUND_GRAY,) * 3)
        dx, dy = (jitter.randint(-JITTER_PIXELS, JITTER_PIXELS) for _ in range(2))
        canvas.paste(face, (left + dx, top + dy))
        buffer = io.BytesIO()
        canvas.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        encoded.append(buffer.getvalue())
    return encoded


def enroll(api_url: str) -> None:
    """Registra a la persona de los cuadros con dos tomas de frente; si ya está, nada."""
    with httpx.Client(base_url=api_url) as api:
        version = api.get("/consent").raise_for_status().json()["version"]
        consent = {"version": version, "accepted": True}
        started = api.post("/enrollments", json={"display_name": NAME, "consent": consent})
        enrollment = started.raise_for_status().json()["enrollment_id"]
        for portrait in (SINGLE_PORTRAIT, OTHER_PORTRAIT):
            shot = api.post(
                f"/enrollments/{enrollment}/shots",
                files={"image": ("toma.jpg", _jpeg(selfie(portrait)), "image/jpeg")},
            )
            if shot.status_code == httpx.codes.CONFLICT and shot.json()["kind"] == ALREADY_ENROLLED:
                return
            shot.raise_for_status()
        api.post(f"/enrollments/{enrollment}/finish").raise_for_status()


async def measure(
    url: str, use_cache: bool, images: Sequence[bytes], warmup: int, count: int, fps: float
) -> Run:
    run = Run()
    async with connect(url, max_size=None) as socket:
        await _receive(socket)
        await socket.send(json.dumps({"type": "hello", "track_cache": use_cache}))
        await _receive(socket)
        for number in range(1, warmup + count + 1):
            started = time.perf_counter()
            await socket.send(encode_frame(number, images[number % len(images)]))
            result = await _receive(socket)
            elapsed = time.perf_counter() - started
            if result["type"] != "result" or result["frame"] != number:
                raise RuntimeError(f"respuesta inesperada al cuadro {number}: {result}")
            if number > warmup:
                run.add(elapsed * MILLISECONDS_PER_SECOND, result)
            await asyncio.sleep(max(0.0, 1 / fps - (time.perf_counter() - started)))
    return run


async def _receive(socket: ClientConnection) -> dict[str, Any]:
    message = await socket.recv()
    return dict(json.loads(message))


def docker_load_average(project: str | None) -> float | None:
    """La carga media del último minuto de la máquina de Docker, leída en el contenedor."""
    if project is None:
        return None
    command = ("docker", "compose", "-p", project, "exec", "-T", "faces-api", *LOADAVG_COMMAND)
    output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    return float(output.split()[0])


def run_benchmark(arguments: argparse.Namespace) -> Report:
    targets = [_target(text) for text in arguments.target]
    portrait = selfie(SINGLE_PORTRAIT)
    images = frames(portrait, DISTINCT_FRAMES, arguments.seed)
    enroll(arguments.api_url)
    report = Report(
        REPORT_NAME,
        {
            "destinos": dict(targets),
            "cuadros": arguments.frames,
            "calentamiento": arguments.warmup,
            "fps": arguments.fps,
            "cuadro": f"{FRAME_SIZE[0]}x{FRAME_SIZE[1]} JPEG q{JPEG_QUALITY}",
            "bytes_por_cuadro_p50": int(np.median([len(image) for image in images])),
            "equipo": machine_description(),
        },
    )
    for label, url in targets:
        for use_cache, operation in ((True, WITH_CACHE), (False, WITHOUT_CACHE)):
            before = (os.getloadavg()[0], docker_load_average(arguments.compose_project))
            run = asyncio.run(
                measure(url, use_cache, images, arguments.warmup, arguments.frames, arguments.fps)
            )
            after = (os.getloadavg()[0], docker_load_average(arguments.compose_project))
            summary = run.summary()
            report.add(
                label,
                operation,
                arguments.frames,
                summary["p50_ms"],
                carga_equipo_antes=round(before[0], 2),
                carga_equipo_despues=round(after[0], 2),
                carga_docker_antes=before[1],
                carga_docker_despues=after[1],
                **summary,
            )
    return report


def _target(text: str) -> tuple[str, str]:
    label, separator, url = text.partition(TARGET_SEPARATOR)
    if not separator or not label or not url:
        raise argparse.ArgumentTypeError(f"se espera etiqueta=url y llegó {text!r}")
    return label, url


def _jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def _percentile(values: Sequence[float], percentile: int) -> float:
    return round(float(np.percentile(values, percentile)), DIGITS)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--api-url", required=True, help="el API por HTTP, para registrar")
    parser.add_argument(
        "--target", action="append", required=True, help="etiqueta=ws://… (se repite)"
    )
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
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
