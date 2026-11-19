"""Servicio de rostros: latencia de registrar una toma y de reconocer según el tamaño de la
galería, y errores del conjunto abierto con LFW.

    .venv/bin/python -m benchmarks.faces_service_benchmark --lfw data/datasets/lfw/lfw \\
        --models data/models/faces --people 1000 --checkpoints 100 250 500 1000 --queries 200

Todo pasa por el API del servicio (`faces.api.create_app`), que habla con el gestor real en
el mismo proceso por `minigestor_client`: cada tiempo es el de una petición HTTP completa,
con el análisis de la foto, la búsqueda en el HNSW coseno de `face_embeddings` y las
escrituras. No hay red entre los dos, así que es el piso de lo que tardará con Docker.

1. Se registran `--people` personas de LFW con al menos dos fotos, tomadas al azar. Cada una
   se registra con una sola toma de frente (el plan `front`), probando sus fotos en orden
   hasta que una pasa los controles; la última queda fuera, para reconocerla después. Quien
   no tiene ninguna que pase se salta, y se cuenta por qué. Antes de cada registro se pide
   `POST /identify` con su primera foto: su mejor coseno es el de un desconocido contra la
   galería de ese momento.
2. Al llegar a cada tamaño de `--checkpoints` se miden `--queries` reconocimientos de fotos
   que no se registraron, de personas registradas y de personas con una sola foto en LFW.

Los umbrales de duplicado se apagan (1,0) para que ninguna persona de LFW quede fuera por
parecerse a otra: lo que el servicio habría rechazado se cuenta aparte, con cada umbral.
"""

from __future__ import annotations

import argparse
import random
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from fastapi.testclient import TestClient

from api.auth import Role, ServiceKey, digest_of
from api.main import create_app as create_engine_app
from benchmarks.common import Report, machine_description, results_directory
from config import EngineConfig
from faces.api import create_app
from faces.api.settings import FacesSettings
from faces.lfw import LfwImage, lfw_images
from faces.pose import Pose
from faces.quality import QualityConfig
from minigestor_client import SERVER_TIMING_HEADER, parse_server_timing
from query.plan import MILLISECONDS_PER_SECOND

REPORT_NAME = "rostros_servicio"
SHOT = "registrar una toma"
FINISH = "terminar el registro"
IDENTIFY = "reconocer"
OPEN_SET = "conjunto abierto"
FALSE_DUPLICATES = "duplicados falsos"
REJECTIONS = "rechazos de calidad"
ACCEPTED = "toma aceptada"
REJECTED = "toma rechazada"
ENROLLED = "persona registrada"
STRANGER = "desconocida"
STAGES = ("analisis", "sesion", "galeria", "guardar", "total")
PERCENTILES = (50, 95)
DEFAULT_PEOPLE = 1000
DEFAULT_CHECKPOINTS = (100, 250, 500, 1000)
DEFAULT_QUERIES = 200
DEFAULT_THRESHOLDS = (0.363, 0.40, 0.45, 0.50)
DEFAULT_SEED = 20261118
DEFAULT_THREADS = 1
DISABLED_THRESHOLD = 1.0
MIN_PHOTOS = 2
ENGINE_URL = "http://gestor"
SERVICE_KEY = "clave-del-benchmark-de-rostros-0123456789"
ADMIN_KEY = "clave-de-administracion-del-benchmark-0123456789"
DIGITS = 3
FRACTION_DIGITS = 5


@dataclass
class Timings:
    """Milisegundos por etapa de las peticiones de un grupo."""

    stages: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def add(self, response: httpx.Response, total_ms: float) -> None:
        timing = parse_server_timing(response.headers.get(SERVER_TIMING_HEADER))
        for stage in STAGES:
            self.stages[stage].append(total_ms if stage == "total" else timing.get(stage, 0.0))

    def summary(self) -> dict[str, Any]:
        totals = self.stages["total"]
        median, high = (round(float(v), DIGITS) for v in np.percentile(totals, PERCENTILES))
        stages = {
            f"{stage}_p{percentile}_ms": round(float(np.percentile(values, percentile)), DIGITS)
            for stage, values in self.stages.items()
            for percentile in PERCENTILES
        }
        return {"p50": median, "p95_ms": high, "consultas": len(totals), **stages}


@dataclass
class Gallery:
    """Lo que se lleva de la ejecución: quién está registrado y las fotos que quedan."""

    enrolled: dict[str, str] = field(default_factory=dict)
    held_out: dict[str, Path] = field(default_factory=dict)
    impostor_scores: list[tuple[int, float]] = field(default_factory=list)
    rejections: Counter[str] = field(default_factory=Counter)


def people_with_photos(images: Sequence[LfwImage]) -> dict[str, list[Path]]:
    photos: dict[str, list[Path]] = defaultdict(list)
    for image in images:
        photos[image.person].append(image.path)
    return photos


@contextmanager
def faces_service(models: Path, data: Path, thresholds: Sequence[float]) -> Iterator[TestClient]:
    """El servicio contra un gestor nuevo en `data`. Reconoce desde el umbral más bajo de
    `thresholds`: los demás se aplican después sobre los cosenos."""
    keys = (
        ServiceKey("faces", Role.WRITE, digest_of(SERVICE_KEY)),
        ServiceKey("faces-migraciones", Role.ADMIN, digest_of(ADMIN_KEY)),
    )
    engine = create_engine_app(EngineConfig(data_directory=data), service_keys=keys)
    settings = FacesSettings.model_validate(
        {
            "engine_url": ENGINE_URL,
            "engine_key": SERVICE_KEY,
            "migration_key": ADMIN_KEY,
            "models_dir": models,
            "enrollment_poses": (Pose.FRONT,),
            "match_threshold": min(thresholds),
            "duplicate_threshold": DISABLED_THRESHOLD,
            "lookalike_threshold": DISABLED_THRESHOLD,
            "analysis_workers": 1,
        }
    )
    app = create_app(settings, engine_transport=httpx.ASGITransport(app=engine))
    try:
        with TestClient(app) as client:
            yield client
    finally:
        engine.state.service.close()


def timed_post(client: TestClient, path: str, **arguments: Any) -> tuple[httpx.Response, float]:
    started = time.perf_counter()
    response = client.post(path, **arguments)
    elapsed = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    response.raise_for_status()
    return response, elapsed


def photo_file(path: Path) -> dict[str, tuple[str, bytes, str]]:
    return {"image": (path.name, path.read_bytes(), "image/jpeg")}


class Run:
    def __init__(self, client: TestClient, report: Report, arguments: argparse.Namespace):
        self.client = client
        self.report = report
        self.arguments = arguments
        self.gallery = Gallery()
        self.consent = {
            "version": client.get("/consent").json()["version"],
            "accepted": True,
        }
        self.shots = {ACCEPTED: Timings(), REJECTED: Timings()}
        self.finishes = Timings()

    def enroll(self, person: str, photos: list[Path]) -> None:
        """Registra a `person` con la primera de sus fotos (menos la última) que pasa."""
        size = len(self.gallery.enrolled)
        probe, _ = timed_post(self.client, "/identify", files=photo_file(photos[0]))
        best = _best_score(probe.json())
        if best is not None:
            self.gallery.impostor_scores.append((size, best))
        body = {"display_name": person.replace("_", " "), "consent": self.consent}
        enrollment = self.client.post("/enrollments", json=body).json()["enrollment_id"]
        for photo in photos[:-1]:
            response, elapsed = timed_post(
                self.client, f"/enrollments/{enrollment}/shots", files=photo_file(photo)
            )
            result = response.json()
            self.shots[ACCEPTED if result["accepted"] else REJECTED].add(response, elapsed)
            if result["accepted"]:
                finished, elapsed = timed_post(self.client, f"/enrollments/{enrollment}/finish")
                self.finishes.add(finished, elapsed)
                self.gallery.enrolled[person] = finished.json()["person"]["person_id"]
                self.gallery.held_out[person] = photos[-1]
                return
            self.gallery.rejections[result["reason"]] += 1
        self.client.delete(f"/enrollments/{enrollment}")

    def measure_checkpoint(self, strangers: Sequence[Path], rng: random.Random) -> None:
        size = len(self.gallery.enrolled)
        known = rng.sample(sorted(self.gallery.held_out), min(self.arguments.queries, size))
        timings = {ENROLLED: Timings(), STRANGER: Timings()}
        genuine: list[tuple[bool, float | None]] = []
        impostors: list[float | None] = []
        for person in known:
            response, elapsed = timed_post(
                self.client, "/identify", files=photo_file(self.gallery.held_out[person])
            )
            timings[ENROLLED].add(response, elapsed)
            genuine.append(_genuine_outcome(response.json(), person))
        for photo in rng.sample(list(strangers), min(self.arguments.queries, len(strangers))):
            response, elapsed = timed_post(self.client, "/identify", files=photo_file(photo))
            timings[STRANGER].add(response, elapsed)
            impostors.append(_best_score(response.json()))
        for group, timing in timings.items():
            add_timings(self.report, group, IDENTIFY, size, timing)
        for threshold in self.arguments.thresholds:
            self.report.add(
                f"umbral {threshold}",
                OPEN_SET,
                size,
                0.0,
                **_open_set(genuine, impostors, threshold),
            )
        print(f"galería de {size} personas medida")

    def finish(self) -> None:
        enrolled = len(self.gallery.enrolled)
        for group, timing in self.shots.items():
            add_timings(self.report, group, SHOT, enrolled, timing)
        add_timings(self.report, ENROLLED, FINISH, enrolled, self.finishes)
        attempts = sum(len(timing.stages["total"]) for timing in self.shots.values())
        self.report.add(
            "LFW",
            REJECTIONS,
            enrolled,
            0.0,
            tomas=attempts,
            rechazadas=sum(self.gallery.rejections.values()),
            **dict(self.gallery.rejections),
        )
        for checkpoint in self.arguments.checkpoints:
            scores = [s for size, s in self.gallery.impostor_scores if size < checkpoint]
            for threshold in self.arguments.thresholds:
                blocked = sum(score >= threshold for score in scores)
                self.report.add(
                    f"umbral {threshold}",
                    FALSE_DUPLICATES,
                    checkpoint,
                    0.0,
                    registros=len(scores),
                    bloqueados=blocked,
                    tasa=round(blocked / len(scores), FRACTION_DIGITS) if scores else 0.0,
                )


def add_timings(
    report: Report, technique: str, operation: str, size: int, timings: Timings
) -> None:
    """La mediana y el resto del resumen de `timings`; nada si no hubo peticiones."""
    if not timings.stages["total"]:
        return
    summary = timings.summary()
    report.add(technique, operation, size, summary.pop("p50"), **summary)


def _best_score(identification: dict[str, Any]) -> float | None:
    faces = [face for face in identification["faces"] if face["score"] is not None]
    return float(faces[0]["score"]) if faces else None


def _genuine_outcome(identification: dict[str, Any], person: str) -> tuple[bool, float | None]:
    """Si el rostro principal se reconoció como `person` (aunque no pase el umbral, se mira
    el mejor candidato) y su coseno."""
    faces = identification["faces"]
    if not faces:
        return False, None
    face = faces[0]
    return face.get("display_name") == person.replace("_", " "), face["score"]


def _open_set(
    genuine: Sequence[tuple[bool, float | None]],
    impostors: Sequence[float | None],
    threshold: float,
) -> dict[str, Any]:
    """FNIR: personas registradas que no se reconocen como ellas mismas con el umbral;
    FPIR: desconocidos que se reconocen como alguien."""
    missed = sum(not right or score is None or score < threshold for right, score in genuine)
    accepted = sum(score is not None and score >= threshold for score in impostors)
    return {
        "fnir": round(missed / len(genuine), FRACTION_DIGITS) if genuine else 0.0,
        "fpir": round(accepted / len(impostors), FRACTION_DIGITS) if impostors else 0.0,
        "registradas": len(genuine),
        "desconocidas": len(impostors),
    }


def run(arguments: argparse.Namespace) -> Report:
    cv2.setNumThreads(arguments.threads)
    photos = people_with_photos(lfw_images(arguments.lfw))
    rng = random.Random(arguments.seed)
    candidates = sorted(person for person, paths in photos.items() if len(paths) >= MIN_PHOTOS)
    chosen = rng.sample(candidates, len(candidates))
    strangers = [paths[0] for paths in photos.values() if len(paths) == 1]
    report = Report(REPORT_NAME, _parameters(arguments, len(candidates), len(strangers)))
    checkpoints = sorted(arguments.checkpoints)
    with (
        tempfile.TemporaryDirectory() as data,
        faces_service(arguments.models, Path(data), arguments.thresholds) as client,
    ):
        measured = Run(client, report, arguments)
        for person in chosen:
            if len(measured.gallery.enrolled) >= arguments.people:
                break
            measured.enroll(person, photos[person])
            if checkpoints and len(measured.gallery.enrolled) >= checkpoints[0]:
                measured.measure_checkpoint(strangers, rng)
                checkpoints.pop(0)
        measured.finish()
    return report


def _parameters(arguments: argparse.Namespace, candidates: int, strangers: int) -> dict[str, Any]:
    quality = QualityConfig()
    return {
        "personas": arguments.people,
        "personas_con_dos_fotos": candidates,
        "personas_con_una_foto": strangers,
        "tamanos": list(arguments.checkpoints),
        "consultas": arguments.queries,
        "umbrales": list(arguments.thresholds),
        "semilla": arguments.seed,
        "hilos_opencv": arguments.threads,
        "ancho_minimo_rostro": quality.min_face_width,
        "nitidez_minima": quality.min_sharpness,
        "equipo": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lfw", type=Path, required=True, help="carpeta lfw/ con las fotos")
    parser.add_argument("--models", type=Path, required=True, help="carpeta de YuNet y SFace")
    parser.add_argument("--people", type=int, default=DEFAULT_PEOPLE)
    parser.add_argument("--checkpoints", type=int, nargs="+", default=list(DEFAULT_CHECKPOINTS))
    parser.add_argument("--queries", type=int, default=DEFAULT_QUERIES)
    parser.add_argument("--thresholds", type=float, nargs="+", default=list(DEFAULT_THRESHOLDS))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    report = run(arguments)
    print(report.render())
    print(f"\nResultados crudos en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
