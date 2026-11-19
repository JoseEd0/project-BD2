"""Recomendador de la tienda (#81): latencia por señal y fusionada, con la caché fría y
caliente, y las compras conjuntas al vuelo frente a materializadas.

    docker run -d --name redis-medida -p 127.0.0.1:6391:6379 redis:8.8.4-alpine
    uv run --no-sync python -m benchmarks.shop_recommend_benchmark --data-dir data/datasets \\
        --models data/models/shop --engine-data-dir <copia de un gestor ya cargado> \\
        --redis-url redis://127.0.0.1:6391/0

Corre contra un gestor de verdad en otro proceso (uvicorn) sobre `--engine-data-dir`, que ya
tiene el catálogo de #80: la carga (`shop.seed`) añade el historial y materializa los pares,
y eso se mide. El API de la tienda corre en este proceso con un Redis de verdad. Para
`--products` productos al azar (con semilla) se pide `GET /productos/{id}/recomendaciones`
dos veces, con una ubicación de Lima: la primera con la caché vacía para ese producto y la
segunda servida por Redis. De cada respuesta fría se toman los milisegundos de cada señal.
Las compras conjuntas al vuelo se miden con el autojoin sobre el historial y sobre el archivo
secuencial de compras. Se anota la carga media del equipo al empezar y al terminar.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from api.settings import IMPORT_DIRECTORIES_VARIABLE, STATEMENT_TIMEOUT_VARIABLE
from benchmarks.common import Report, load_average, machine_description, results_directory
from benchmarks.engine_process import engine_server
from minigestor_client import AsyncClient, RetryPolicy, Timeouts
from query.plan import MILLISECONDS_PER_SECOND
from shop.api import create_app
from shop.catalog.rows import DatasetPaths, product_rows
from shop.catalog.schema import HISTORY, PURCHASES
from shop.recommend import queries
from shop.recommend.cache import redis_client
from shop.seed import run_seed
from shop.settings import DATASET_DIRECTORY, SeedSettings, ShopSettings

REPORT_NAME = "tienda_recomendaciones"
COLD = "caché fría"
WARM = "caché caliente"
FUSED = "recomendación completa"
COPURCHASE_STRATEGY = "compras conjuntas"
ON_THE_FLY_SEQUENTIAL = "al vuelo sobre shop_compras (secuencial)"
ON_THE_FLY_HISTORY = "al vuelo sobre shop_historial (B+)"
MATERIALIZED = "materializada (hash de shop_compras_conjuntas)"
MATERIALIZATION = "materializar los pares (carga)"
MEDIAN_FIELD = "p50_ms"
P95_FIELD = "p95_ms"
P95_EXTRA_FIELD = "p95_menos_p50_ms"
SECONDS_FIELD = "segundos"
UNUSED_KEY = "sin-clave"
CACHE_HEADER = "X-Cache"
PERCENTILES = 100
P95 = 95
DIGITS = 3
LIMA = {"lat": -12.0464, "lon": -77.0428}
LONG_TIMEOUT_SECONDS = 3600.0
SPLIT = "train"
ON_THE_FLY_LIMIT = 50
EMBEDDING_DIMENSION = 512
HTTP_LOGGER = "httpx"


@dataclass(frozen=True, slots=True)
class Arguments:
    data_dir: Path
    models: Path
    engine_data_dir: Path
    redis_url: str
    products: int
    on_the_fly_products: int
    sequential_products: int
    seed: int
    output: Path


class NoTextEncoder:
    """Las recomendaciones no usan la torre de texto de CLIP (solo la búsqueda de fotos)."""

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), EMBEDDING_DIMENSION), dtype=np.float32)


def run(arguments: Arguments) -> Report:
    report = Report(REPORT_NAME, _parameters(arguments))
    environment = {
        IMPORT_DIRECTORIES_VARIABLE: str(arguments.data_dir.resolve()),
        STATEMENT_TIMEOUT_VARIABLE: str(int(LONG_TIMEOUT_SECONDS)),
    }
    products = _sample(arguments)
    with engine_server(arguments.engine_data_dir, environment) as url:
        seed = asyncio.run(_seed(arguments, url))
        if seed.copurchases is not None:
            step = seed.copurchases
            report.add(
                MATERIALIZATION,
                COPURCHASE_STRATEGY,
                step.pairs,
                _ms(step.seconds),
                **{SECONDS_FIELD: round(step.seconds, DIGITS), "consultas": step.queries,
                   "ya_estaba": step.skipped},
            )
        on_the_fly = asyncio.run(_on_the_fly(url, products, arguments))
        for technique, timings in on_the_fly.items():
            report.add(technique, COPURCHASE_STRATEGY, len(timings), _median(timings),
                       **_summary(timings))
        cold, warm, signals = _recommendations(url, arguments, products)
    report.add(FUSED, COLD, len(cold), _median(cold), **_summary(cold))
    report.add(FUSED, WARM, len(warm), _median(warm), **_summary(warm))
    for signal, timings in sorted(signals.items()):
        report.add(signal, COLD, len(timings), _median(timings), **_summary(timings))
    copurchase = signals.get("compras", [])
    report.add(MATERIALIZED, COPURCHASE_STRATEGY, len(copurchase), _median(copurchase),
               **_summary(copurchase))
    report.parameters["carga_media_al_terminar"] = load_average()
    return report


def _sample(arguments: Arguments) -> list[str]:
    dataset = arguments.data_dir / DATASET_DIRECTORY
    paths = DatasetPaths(dataset, dataset)
    ids = sorted(str(row["id"]) for row in product_rows(paths))
    return random.Random(arguments.seed).sample(ids, arguments.products)


async def _seed(arguments: Arguments, url: str) -> Any:
    settings = SeedSettings(
        engine_url=url,  # type: ignore[arg-type]
        engine_key=UNUSED_KEY,  # type: ignore[arg-type]
        models_dir=arguments.models,
        data_dir=arguments.data_dir,
        job_timeout_seconds=LONG_TIMEOUT_SECONDS,
    )
    timeouts = Timeouts(read_seconds=LONG_TIMEOUT_SECONDS, load_seconds=LONG_TIMEOUT_SECONDS)
    async with AsyncClient(url, UNUSED_KEY, timeouts=timeouts) as engine:
        return await run_seed(engine, settings)


async def _on_the_fly(
    url: str, products: list[str], arguments: Arguments
) -> dict[str, list[float]]:
    timeouts = Timeouts(read_seconds=LONG_TIMEOUT_SECONDS)
    plans = (
        (ON_THE_FLY_HISTORY, HISTORY, arguments.on_the_fly_products),
        (ON_THE_FLY_SEQUENTIAL, PURCHASES, arguments.sequential_products),
    )
    timings: dict[str, list[float]] = defaultdict(list)
    async with AsyncClient(url, UNUSED_KEY, timeouts=timeouts, retry=RetryPolicy(1)) as engine:
        for technique, table, count in plans:
            for product in products[:count]:
                started = time.perf_counter()
                await engine.fetch_all(
                    queries.copurchases_on_the_fly(table), [product, SPLIT, ON_THE_FLY_LIMIT]
                )
                timings[technique].append(_elapsed_ms(started))
                logging.info("%s %s: %.0f ms", technique, product, timings[technique][-1])
    return timings


def _recommendations(
    url: str, arguments: Arguments, products: list[str]
) -> tuple[list[float], list[float], dict[str, list[float]]]:
    settings = ShopSettings(
        engine_url=url,  # type: ignore[arg-type]
        engine_key=UNUSED_KEY,  # type: ignore[arg-type]
        models_dir=arguments.models,
        max_search_limit=ON_THE_FLY_LIMIT,
    )
    redis = redis_client(arguments.redis_url, settings.redis_timeout_seconds)
    app = create_app(settings, text_encoder=NoTextEncoder(), cache_client=redis)
    cold: list[float] = []
    warm: list[float] = []
    signals: dict[str, list[float]] = defaultdict(list)
    with TestClient(app) as shop:
        for product in products:
            path = f"/productos/{product}/recomendaciones"
            for timings, expected in ((cold, "miss"), (warm, "hit")):
                started = time.perf_counter()
                response = shop.get(path, params=LIMA)
                timings.append(_elapsed_ms(started))
                response.raise_for_status()
                if response.headers[CACHE_HEADER] != expected:
                    raise SystemExit(f"{product}: se esperaba {expected} en la caché")
                if expected == "miss":
                    for signal in response.json()["senales"]:
                        signals[signal["senal"]].append(signal["milisegundos"])
    return cold, warm, signals


def _summary(timings: list[float]) -> dict[str, float]:
    if not timings:
        return {}
    median = _median(timings)
    p95 = _p95(timings)
    return {
        MEDIAN_FIELD: median,
        P95_FIELD: p95,
        P95_EXTRA_FIELD: round(p95 - median, DIGITS),
        SECONDS_FIELD: round(median / MILLISECONDS_PER_SECOND, DIGITS),
    }


def _median(timings: list[float]) -> float:
    return round(statistics.median(timings), DIGITS) if timings else 0.0


def _p95(timings: list[float]) -> float:
    if len(timings) == 1:
        return round(timings[0], DIGITS)
    return round(statistics.quantiles(timings, n=PERCENTILES)[P95 - 1], DIGITS)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _ms(seconds: float) -> float:
    return round(seconds * MILLISECONDS_PER_SECOND, DIGITS)


def _parameters(arguments: Arguments) -> dict[str, Any]:
    return {
        "gestor": "uvicorn en otro proceso, con el catálogo de #80 ya cargado",
        "productos": arguments.products,
        "al_vuelo_historial": arguments.on_the_fly_products,
        "al_vuelo_secuencial": arguments.sequential_products,
        "semilla": arguments.seed,
        "ubicacion": LIMA,
        "maquina": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--engine-data-dir", type=Path, required=True)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--products", type=int, default=100)
    parser.add_argument("--on-the-fly-products", type=int, default=5)
    parser.add_argument("--sequential-products", type=int, default=1)
    parser.add_argument("--seed", type=int, default=81)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    parsed = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger(HTTP_LOGGER).setLevel(logging.WARNING)
    report = run(Arguments(**vars(parsed)))
    print(report.render())
    print(f"resultados en {report.save(parsed.output)}")


if __name__ == "__main__":
    main()
