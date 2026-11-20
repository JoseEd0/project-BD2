"""Prueba de carga: peticiones por segundo y latencia p50/p95/p99 de cada app con 1, 2 y 4
réplicas de su API frente a un solo gestor.

    uv run --no-sync python -m benchmarks.load_benchmark \\
        --shop-engine-data data/tienda-gestor --papers-engine-data data/engine-rag \\
        --copyright-engine-data data/copyright-engine --datasets-dir data/datasets \\
        --replicas 1 2 4 --vus 32 --duration 30 --warmup 10

Levanta el compose raíz con `load.compose.yaml` en su propio proyecto (`--compose-project`),
una app a la vez, sobre una copia de los datos que esa app ya cargó en el gestor (un volumen
propio por app, que se borra al terminar). Rostros arranca vacío y registra a una persona.
Cada escenario corre en k6 (imagen oficial, fijada en `--k6-image`) desde un contenedor en la
red de la app y entra por su nginx, como un navegador: `--vus` usuarios que piden sin pausa
durante `--duration` segundos, después de `--warmup` segundos que no se cuentan.

Durante cada medida se lee el tiempo de CPU del cgroup de cada contenedor al empezar y al
terminar: núcleos ocupados = segundos de CPU / segundos de reloj. Se anota la carga media del
anfitrión y la de la máquina virtual de Docker antes y después.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import soundfile
from apps.faces.tests.faces_support import SINGLE_PORTRAIT, selfie

from benchmarks.common import Report, load_average, machine_description, results_directory
from benchmarks.faces_stream_benchmark import enroll, jpeg_of
from songcheck.catalog import CATALOG_FILE, dataset_directory, read_catalog

REPORT_NAME = "carga_replicas"
OPERATION = "carga"
ROOT = Path(__file__).resolve().parent.parent
K6_SCRIPT_DIRECTORY = Path(__file__).resolve().parent / "load"
K6_SCRIPT = "requests.js"
COMPOSE_FILES = (ROOT / "compose.yaml", Path(__file__).resolve().parent / "load.compose.yaml")
DEFAULT_PROJECT = "carga"
DEFAULT_K6_IMAGE = "grafana/k6:1.2.3"
DEFAULT_REPLICAS = (1, 2, 4)
DEFAULT_VUS = 32
DEFAULT_DURATION_SECONDS = 30
DEFAULT_WARMUP_SECONDS = 10
DEFAULT_SEED = 95
WEB_PORT = 8080
WAIT_TIMEOUT_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 120.0
# Lima: la cercanía de la tienda busca stock alrededor de este punto. Cada petición fría lo
# corre una millonésima de grado (~0,1 m), que no cambia el resultado pero sí la clave de la
# caché: así ninguna petición fría encuentra la respuesta de otra.
LIMA = (-12.0464, -77.0428)
COLD_JITTER_DEGREES = 1e-6
COLD_REQUESTS = 40_000
WARM_PRODUCTS = 50
SHOP_SEARCH_TERMS = (
    "guitar",
    "bass",
    "drum",
    "piano",
    "keyboard",
    "violin",
    "ukulele",
    "microphone",
    "cable",
    "amplifier",
    "pedal",
    "strings",
    "stand",
    "headphones",
    "capo",
    "strap",
)
SHOP_SEARCH_LIMIT = 50
SHOP_PRODUCTS = 400
PAPERS_QUESTIONS = Path(__file__).resolve().parent / "rag" / "preguntas.jsonl"
PAPERS_RESULTS = 10
COPYRIGHT_CLIP_START_SECONDS = 5
COPYRIGHT_CLIP_SECONDS = 10
WAV_TYPE = "audio/wav"
WAV_SAMPLES = "int16"
WAV_SUBTYPE = "PCM_16"
COPYRIGHT_CLIP = "fragmento.wav"
COPYRIGHT_SONGS = 500
SCENARIOS = "escenarios"
CGROUP_CPU_STAT = "/sys/fs/cgroup/cpu.stat"
CPU_USAGE_FIELD = "usage_usec"
MICROSECONDS_PER_SECOND = 1_000_000.0
PROC_LOADAVG = "/proc/loadavg"
PROC_SOCKSTAT = "/proc/net/sockstat"
TCP_SOCKSTAT = "TCP:"
TIME_WAIT_FIELD = "tw"
PERCENTILE_FIELDS = (("med", "p50_ms"), ("p(95)", "p95_ms"), ("p(99)", "p99_ms"))
DIGITS = 2
CACHE_RESULTS = ("hit", "miss")
STATUS_CLASSES = ("2xx", "3xx", "4xx", "5xx", "sin_respuesta")
EMPTY: dict[str, Any] = {"values": {"count": 0}}


@dataclass(frozen=True, slots=True)
class Scenario:
    """Una ruta que se mide: su nombre en los resultados, su archivo para k6 y las etapas de
    `Server-Timing` que se guardan. `prime` corre antes de cada medida (sin contar)."""

    label: str
    key: str
    stages: tuple[str, ...]
    flush_cache: bool = False
    prime: bool = False


@dataclass(frozen=True, slots=True)
class App:
    """Una aplicación: el prefijo de sus servicios en el compose y sus escenarios."""

    name: str
    service: str
    scenarios: tuple[Scenario, ...]

    @property
    def api(self) -> str:
        return f"{self.service}-api"

    @property
    def web(self) -> str:
        return f"{self.service}-web"


SHOP_COLD = Scenario(
    "tienda: recomendación, caché fría", "tienda-fria", ("cache", "total"), flush_cache=True
)
SHOP_WARM = Scenario(
    "tienda: recomendación, caché caliente", "tienda-caliente", ("cache", "total"), prime=True
)
PAPERS_SEARCH = Scenario(
    "papers: /search híbrida",
    "papers-busqueda",
    ("embedding", "bm25", "vector", "papers", "total"),
)
# Una canción del catálogo por su id (la fila por la clave primaria y sus tramos por rango).
# `GET /catalog` no entra: sus `COUNT(*)` recorren las dos tablas enteras en cada petición.
COPYRIGHT_SONG = Scenario("copyright: /songs/{id}", "copyright-cancion", ("gestor", "total"))
COPYRIGHT_DETECTION = Scenario(
    "copyright: detección de 10 s",
    "copyright-deteccion",
    ("probe", "segments", "decode", "extraction", "search", "songs", "total"),
)
FACES_IDENTIFY = Scenario(
    "rostros: /identify", "rostros-identificar", ("analisis", "galeria", "total")
)

APPS = {
    "shop": App("shop", "shop", (SHOP_COLD, SHOP_WARM)),
    "papers": App("papers", "papers", (PAPERS_SEARCH,)),
    "copyright": App("copyright", "copyright", (COPYRIGHT_SONG, COPYRIGHT_DETECTION)),
    "faces": App("faces", "faces", (FACES_IDENTIFY,)),
}
WEB_PORT_VARIABLES = ("SHOP", "COPYRIGHT", "PAPERS", "FACES")


class Deployment:
    """El proyecto de compose de la prueba: levantar una app con N réplicas, leer sus
    contenedores y apagarlo."""

    def __init__(self, project: str, keys_file: Path, environment: dict[str, str]) -> None:
        self.project = project
        self._keys_file = keys_file
        self._environment = environment

    def compose(self, *arguments: str, capture: bool = False) -> str:
        files = [part for path in COMPOSE_FILES for part in ("-f", str(path))]
        command = [
            "docker",
            "compose",
            "-p",
            self.project,
            "--env-file",
            str(self._keys_file),
            *files,
            *arguments,
        ]
        completed = subprocess.run(
            command, env=self._environment, check=True, capture_output=capture, text=True
        )
        return completed.stdout if capture else ""

    def build(self, app: App) -> None:
        self.compose("build", "engine", app.api, app.web)

    def up(self, app: App, replicas: int) -> None:
        self.compose(
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            str(WAIT_TIMEOUT_SECONDS),
            "--scale",
            f"{app.api}={replicas}",
            app.web,
        )

    def use_engine_volume(self, volume: str) -> None:
        self._environment["LOAD_ENGINE_VOLUME"] = volume

    def containers(self, service: str) -> list[str]:
        return self.compose("ps", "--quiet", service, capture=True).split()

    def api_url(self, app: App) -> str:
        published = self.compose("port", app.web, str(WEB_PORT), capture=True).strip()
        return f"http://{published}/api"

    def network(self, app: App) -> str:
        return f"{self.project}_{app.service}"

    def down(self) -> None:
        self.compose("down", "--remove-orphans")


def docker_output(*arguments: str) -> str:
    return subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True).stdout


def cpu_seconds(container: str) -> float:
    """Segundos de CPU que lleva el cgroup del contenedor."""
    for line in docker_output("exec", container, "cat", CGROUP_CPU_STAT).splitlines():
        field, value = line.split()
        if field == CPU_USAGE_FIELD:
            return int(value) / MICROSECONDS_PER_SECOND
    raise RuntimeError(f"{container} no dice {CPU_USAGE_FIELD} en {CGROUP_CPU_STAT}")


def time_wait_sockets(container: str) -> int:
    """Conexiones TCP del contenedor en TIME_WAIT: cada petición que nginx reenvía abre una
    conexión nueva hacia la API, y cada una ocupa un puerto local unos 60 s al cerrarse."""
    for line in docker_output("exec", container, "cat", PROC_SOCKSTAT).splitlines():
        if line.startswith(TCP_SOCKSTAT):
            fields = line.split()
            return int(fields[fields.index(TIME_WAIT_FIELD) + 1])
    raise RuntimeError(f"{container} no dice sus conexiones TCP en {PROC_SOCKSTAT}")


def docker_load_average(container: str) -> float:
    """La carga media del último minuto de la máquina virtual de Docker."""
    return float(docker_output("exec", container, "cat", PROC_LOADAVG).split()[0])


@dataclass
class CpuWindow:
    """Núcleos que ocupó cada grupo de contenedores mientras duró el bloque."""

    groups: dict[str, list[str]]
    cores: dict[str, float] | None = None

    @contextmanager
    def measuring(self) -> Iterator[CpuWindow]:
        before = {name: sum(map(cpu_seconds, ids)) for name, ids in self.groups.items()}
        started = time.monotonic()
        yield self
        elapsed = time.monotonic() - started
        self.cores = {
            name: round((sum(map(cpu_seconds, ids)) - before[name]) / elapsed, DIGITS)
            for name, ids in self.groups.items()
        }

    def measured(self) -> dict[str, float]:
        if self.cores is None:
            raise RuntimeError("la ventana de CPU no se midió")
        return self.cores


def run_k6(
    arguments: argparse.Namespace,
    deployment: Deployment,
    app: App,
    scenario: Scenario,
    workspace: Path,
    seconds: int,
) -> dict[str, Any]:
    """Corre el escenario en k6 y devuelve su resumen."""
    output = workspace / "salida"
    output.mkdir(exist_ok=True)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            deployment.network(app),
            "--volume",
            f"{K6_SCRIPT_DIRECTORY}:/scripts:ro",
            "--volume",
            f"{workspace / SCENARIOS}:/fixtures:ro",
            "--volume",
            f"{output}:/out",
            "--env",
            f"BASE_URL=http://{app.web}:{WEB_PORT}/api",
            "--env",
            f"SCENARIO={scenario.key}",
            "--env",
            f"STAGES={','.join(scenario.stages)}",
            "--env",
            f"VUS={arguments.vus}",
            "--env",
            f"DURATION={seconds}s",
            arguments.k6_image,
            "run",
            "--quiet",
            f"/scripts/{K6_SCRIPT}",
        ],
        check=True,
    )
    summary: dict[str, Any] = json.loads((output / f"{scenario.key}.json").read_text())
    return summary


def write_scenario(workspace: Path, scenario: Scenario, content: dict[str, Any]) -> None:
    directory = workspace / SCENARIOS
    directory.mkdir(exist_ok=True)
    (directory / f"{scenario.key}.json").write_text(json.dumps(content), encoding="utf-8")


def shop_products(api: httpx.Client) -> list[str]:
    found: dict[str, None] = {}
    for term in SHOP_SEARCH_TERMS:
        response = api.get("/busqueda/texto", params={"q": term, "limite": SHOP_SEARCH_LIMIT})
        found.update((item["producto"]["id"], None) for item in response.raise_for_status().json())
        if len(found) >= SHOP_PRODUCTS:
            break
    return list(found)[:SHOP_PRODUCTS]


def recommendation_path(product: str, latitude: float, longitude: float) -> str:
    return f"/productos/{product}/recomendaciones?lat={latitude:.7f}&lon={longitude:.7f}"


def prepare_shop(api: httpx.Client, workspace: Path, seed: int, datasets: Path) -> dict[str, Any]:
    products = shop_products(api)
    random.Random(seed).shuffle(products)
    cold = [
        {
            "path": recommendation_path(
                products[index % len(products)],
                LIMA[0] + COLD_JITTER_DEGREES * (index // len(products)),
                LIMA[1],
            )
        }
        for index in range(COLD_REQUESTS)
    ]
    warm = [{"path": recommendation_path(product, *LIMA)} for product in products[:WARM_PRODUCTS]]
    write_scenario(workspace, SHOP_COLD, {"method": "GET", "requests": cold})
    write_scenario(workspace, SHOP_WARM, {"method": "GET", "requests": warm})
    return {
        "productos": len(products),
        "peticiones_frias": len(cold),
        "productos_calientes": len(warm),
    }


def prepare_papers(api: httpx.Client, workspace: Path, seed: int, datasets: Path) -> dict[str, Any]:
    lines = PAPERS_QUESTIONS.read_text(encoding="utf-8").splitlines()
    questions = [json.loads(line)["question"] for line in lines if line.strip()]
    random.Random(seed).shuffle(questions)
    requests = [
        {"path": "/search", "body": {"query": question, "mode": "hybrid", "k": PAPERS_RESULTS}}
        for question in questions
    ]
    write_scenario(workspace, PAPERS_SEARCH, {"method": "POST", "requests": requests})
    return {"preguntas": len(requests), "modo": "hybrid", "k": PAPERS_RESULTS}


def prepare_copyright(
    api: httpx.Client, workspace: Path, seed: int, datasets: Path
) -> dict[str, Any]:
    """Canciones del catálogo al azar y, para la detección, `COPYRIGHT_CLIP_SECONDS` de una
    de ellas en WAV. Se leen del dataset del anfitrión: el gestor guarda las rutas con que se
    cargó."""
    folder = dataset_directory(datasets)
    generator = random.Random(seed)
    catalog = read_catalog(folder / CATALOG_FILE)
    songs = generator.sample(catalog, min(COPYRIGHT_SONGS, len(catalog)))
    requests = [{"path": f"/songs/{song.track_id}"} for song in songs]
    write_scenario(workspace, COPYRIGHT_SONG, {"method": "GET", "requests": requests})
    clip = wav_fragment(folder / songs[0].path)
    (workspace / SCENARIOS / COPYRIGHT_CLIP).write_bytes(clip)
    upload = {"field": "file", "name": COPYRIGHT_CLIP, "type": WAV_TYPE, "file": COPYRIGHT_CLIP}
    write_scenario(
        workspace,
        COPYRIGHT_DETECTION,
        {"method": "POST", "requests": [{"path": "/detections"}], "upload": upload},
    )
    detection = api.post("/detections", files={"file": (COPYRIGHT_CLIP, clip, WAV_TYPE)})
    found = detection.raise_for_status().json()
    return {
        "canciones": len(requests),
        "fragmento_de": songs[0].track_id,
        "fragmento_bytes": len(clip),
        "fragmento_segundos": found["duration_seconds"],
        "fragmento_detectado": found["detected"],
    }


def wav_fragment(track: Path) -> bytes:
    """`COPYRIGHT_CLIP_SECONDS` de `track` desde `COPYRIGHT_CLIP_START_SECONDS`, en WAV."""
    with soundfile.SoundFile(track) as audio:
        audio.seek(COPYRIGHT_CLIP_START_SECONDS * audio.samplerate)
        samples = audio.read(COPYRIGHT_CLIP_SECONDS * audio.samplerate, dtype=WAV_SAMPLES)
        rate = audio.samplerate
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, rate, format="WAV", subtype=WAV_SUBTYPE)
    return buffer.getvalue()


def prepare_faces(api: httpx.Client, workspace: Path, seed: int, datasets: Path) -> dict[str, Any]:
    enroll(str(api.base_url).rstrip("/"))
    (workspace / SCENARIOS).mkdir(exist_ok=True)
    photo = jpeg_of(selfie(SINGLE_PORTRAIT))
    (workspace / SCENARIOS / "selfie.jpg").write_bytes(photo)
    upload = {"field": "image", "name": "selfie.jpg", "type": "image/jpeg", "file": "selfie.jpg"}
    write_scenario(
        workspace,
        FACES_IDENTIFY,
        {"method": "POST", "requests": [{"path": "/identify"}], "upload": upload},
    )
    identified = api.post("/identify", files={"image": ("selfie.jpg", photo, "image/jpeg")})
    return {"foto_bytes": len(photo), "respuesta": identified.raise_for_status().json()}


PREPARE: dict[str, Callable[[httpx.Client, Path, int, Path], dict[str, Any]]] = {
    "shop": prepare_shop,
    "papers": prepare_papers,
    "copyright": prepare_copyright,
    "faces": prepare_faces,
}


def engine_data_of(arguments: argparse.Namespace, app: App) -> Path | None:
    source: Path | None = getattr(arguments, f"{app.name}_engine_data")
    return source


@contextmanager
def engine_volume(name: str, source: Path | None, image: str) -> Iterator[None]:
    """El volumen `name` con la copia de `source` (vacío si no hay), borrado al salir."""
    docker_output("volume", "create", name)
    try:
        if source is not None:
            docker_output(
                "run",
                "--rm",
                "--volume",
                f"{name}:/data",
                "--volume",
                f"{source.resolve()}:/src:ro",
                image,
                "sh",
                "-c",
                "cp -R /src/. /data/",
            )
        yield
    finally:
        docker_output("volume", "rm", "--force", name)


def flush_cache(deployment: Deployment) -> None:
    for container in deployment.containers("redis"):
        docker_output("exec", container, "redis-cli", "FLUSHALL")


def prime(api: httpx.Client, workspace: Path, scenario: Scenario) -> None:
    content = json.loads((workspace / SCENARIOS / f"{scenario.key}.json").read_text())
    for request in content["requests"]:
        api.get(request["path"]).raise_for_status()


def counted(metrics: dict[str, Any], name: str) -> int:
    """La cuenta de un contador de k6, o 0 si no contó nada."""
    return int(metrics.get(name, EMPTY)["values"]["count"])


def measurement_of(
    summary: dict[str, Any], replicas: int, cpu: dict[str, float], loads: dict[str, float]
) -> dict[str, Any]:
    metrics = summary["metrics"]
    duration = metrics["http_req_duration"]["values"]
    extra: dict[str, Any] = {
        "replicas": replicas,
        "rps": round(metrics["http_reqs"]["values"]["rate"], DIGITS),
        "peticiones": int(metrics["http_reqs"]["values"]["count"]),
        "fallidas": round(metrics["http_req_failed"]["values"]["rate"], 4),
    }
    extra["rps_correctas"] = round(
        extra["rps"] * (1 - metrics["http_req_failed"]["values"]["rate"]), DIGITS
    )
    extra.update((field, round(duration[key], DIGITS)) for key, field in PERCENTILE_FIELDS)
    for stage, trend in summary["stages"].items():
        if trend is not None:
            extra[f"{stage}_p50_ms"] = round(trend["values"]["med"], DIGITS)
    cache = {
        result: counted(metrics, f"cache_results{{result:{result}}}") for result in CACHE_RESULTS
    }
    if any(cache.values()):
        extra.update((f"cache_{result}", count) for result, count in cache.items())
    extra.update(
        (f"respuestas_{status}", count)
        for status in STATUS_CLASSES
        if (count := counted(metrics, f"responses{{status:{status}}}")) > 0
    )
    extra.update((f"cpu_{group}_nucleos", cores) for group, cores in cpu.items())
    extra.update(loads)
    return extra


def measure_scenario(
    arguments: argparse.Namespace,
    deployment: Deployment,
    app: App,
    scenario: Scenario,
    replicas: int,
    workspace: Path,
) -> dict[str, Any]:
    api = httpx.Client(base_url=deployment.api_url(app), timeout=HTTP_TIMEOUT_SECONDS)
    with api:
        if arguments.warmup > 0:
            run_k6(arguments, deployment, app, scenario, workspace, arguments.warmup)
        if scenario.flush_cache:
            flush_cache(deployment)
        if scenario.prime:
            prime(api, workspace, scenario)
    engine = deployment.containers("engine")
    window = CpuWindow(
        {
            "gestor": engine,
            "api": deployment.containers(app.api),
            "nginx": deployment.containers(app.web),
            "redis": deployment.containers("redis"),
        }
    )
    loads = {
        "carga_anfitrion_antes": load_average(),
        "carga_docker_antes": docker_load_average(engine[0]),
    }
    with window.measuring():
        summary = run_k6(arguments, deployment, app, scenario, workspace, arguments.duration)
    loads.update(
        carga_anfitrion_despues=load_average(), carga_docker_despues=docker_load_average(engine[0])
    )
    extra = measurement_of(summary, replicas, window.measured(), loads)
    extra["nginx_time_wait"] = time_wait_sockets(deployment.containers(app.web)[0])
    return extra


def measure_app(
    arguments: argparse.Namespace, deployment: Deployment, app: App, report: Report
) -> None:
    volume = f"{deployment.project}-{app.name}-gestor"
    deployment.use_engine_volume(volume)
    deployment.build(app)
    with engine_volume(volume, engine_data_of(arguments, app), arguments.engine_image):
        try:
            with tempfile.TemporaryDirectory(prefix="carga-") as directory:
                workspace = Path(directory)
                measure_replicas(arguments, deployment, app, workspace, report)
        finally:
            deployment.down()


def measure_replicas(
    arguments: argparse.Namespace,
    deployment: Deployment,
    app: App,
    workspace: Path,
    report: Report,
) -> None:
    for position, replicas in enumerate(arguments.replicas):
        deployment.up(app, replicas)
        if position == 0:
            with httpx.Client(
                base_url=deployment.api_url(app), timeout=HTTP_TIMEOUT_SECONDS
            ) as api:
                facts = PREPARE[app.name](api, workspace, arguments.seed, arguments.datasets_dir)
            report.parameters.setdefault("preparacion", {})[app.name] = facts
        for scenario in app.scenarios:
            extra = measure_scenario(arguments, deployment, app, scenario, replicas, workspace)
            report.add(scenario.label, OPERATION, replicas, extra["p50_ms"], **extra)
            print(f"{scenario.label} con {replicas} réplicas: {extra}", flush=True)


def write_keys(keys_file: Path) -> None:
    """Las claves del gestor en un archivo propio, no en el `.env` del repositorio."""
    subprocess.run(
        [sys.executable, "-m", "tools.compose_keys", "--env-file", str(keys_file)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def compose_environment(arguments: argparse.Namespace) -> dict[str, str]:
    """Las variables del compose: los datasets y un puerto libre en localhost por web."""
    environment = dict(os.environ)
    datasets = str(arguments.datasets_dir.resolve())
    environment.update(MINIGESTOR_DATASETS_DIR=datasets, PAPERS_DATASETS_DIR=datasets)
    for prefix in WEB_PORT_VARIABLES:
        environment[f"{prefix}_WEB_PORT"] = "127.0.0.1:"
        environment[f"{prefix}_WEB_TLS_PORT"] = "127.0.0.1:"
    return environment


def docker_machine() -> dict[str, Any]:
    cpus, memory, version = docker_output(
        "info", "--format", "{{.NCPU}} {{.MemTotal}} {{.ServerVersion}}"
    ).split()
    return {
        "docker_cpus": int(cpus),
        "docker_memoria_bytes": int(memory),
        "docker_version": version,
    }


def run_benchmark(arguments: argparse.Namespace) -> Report:
    report = Report(
        REPORT_NAME,
        {
            "apps": arguments.apps,
            "replicas": arguments.replicas,
            "vus": arguments.vus,
            "duracion_s": arguments.duration,
            "calentamiento_s": arguments.warmup,
            "k6": arguments.k6_image,
            "semilla": arguments.seed,
            "equipo": machine_description(),
            **docker_machine(),
        },
    )
    with tempfile.TemporaryDirectory(prefix="carga-claves-") as directory:
        keys_file = Path(directory) / "claves.env"
        write_keys(keys_file)
        deployment = Deployment(
            arguments.compose_project, keys_file, compose_environment(arguments)
        )
        for name in arguments.apps:
            measure_app(arguments, deployment, APPS[name], report)
    return report


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--apps", nargs="+", choices=list(APPS), default=list(APPS))
    parser.add_argument("--replicas", nargs="+", type=int, default=list(DEFAULT_REPLICAS))
    parser.add_argument("--vus", type=int, default=DEFAULT_VUS, help="usuarios virtuales de k6")
    parser.add_argument(
        "--duration", type=int, default=DEFAULT_DURATION_SECONDS, help="segundos medidos"
    )
    parser.add_argument(
        "--warmup", type=int, default=DEFAULT_WARMUP_SECONDS, help="segundos sin contar"
    )
    parser.add_argument(
        "--shop-engine-data", type=Path, help="carpeta del gestor con la tienda cargada"
    )
    parser.add_argument(
        "--papers-engine-data", type=Path, help="carpeta del gestor con el RAG cargado"
    )
    parser.add_argument(
        "--copyright-engine-data", type=Path, help="carpeta del gestor con el catálogo de copyright"
    )
    parser.add_argument("--faces-engine-data", type=Path, help="por omisión, un gestor vacío")
    parser.add_argument("--datasets-dir", type=Path, required=True, help="data/datasets")
    parser.add_argument("--compose-project", default=DEFAULT_PROJECT)
    parser.add_argument("--k6-image", default=DEFAULT_K6_IMAGE)
    parser.add_argument("--engine-image", default=f"{DEFAULT_PROJECT}-engine")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
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
