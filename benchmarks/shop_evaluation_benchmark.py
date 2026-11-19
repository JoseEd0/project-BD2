"""Evaluación del recomendador de la tienda (#82) con la partición `last_out`: de cada
usuario se deja fuera la penúltima compra (validación) y la última (prueba), se recomienda a
partir de sus compras de entrenamiento y se mira si la compra que falta está en el top-k.

    uv run --no-sync python -m benchmarks.shop_evaluation_benchmark --data-dir data/datasets \\
        --engine-data-dir <copia de un gestor cargado con shop.seed> \\
        --signals <archivo de candidatos> --users 5000

Corre el recomendador de verdad (`shop.recommend.Recommender`) contra un gestor en otro
proceso, cuyas compras conjuntas se materializaron solo con el entrenamiento (lo comprueba
en `shop_materializaciones`). Para una muestra de usuarios con semilla guarda en `--signals`
los candidatos de cada señal, con CLIP y con SIFT; todo lo demás es Python sobre esas listas:

- cada señal sola, la fusión con los pesos por omisión, lo más vendido y una lista al azar;
- los pesos de la fusión, buscados en una rejilla sobre el símplex **solo con la validación**,
  y medidos una vez en la prueba; la diferencia con los de omisión, con intervalo pareado;
- el arranque en frío por grupos: usuarios con pocas compras de entrenamiento, productos
  con pocas compras y fusiones sin compras conjuntas;
- CLIP contra SIFT como señal visual, sola y fundida con compras y texto (con los pesos de
  cada una ajustados en la validación).

Los intervalos son bootstrap por percentiles sobre los usuarios. Se anota la carga media del
equipo al empezar y al terminar.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from api.settings import IMPORT_DIRECTORIES_VARIABLE, STATEMENT_TIMEOUT_VARIABLE
from benchmarks.common import Report, load_average, machine_description, results_directory
from benchmarks.engine_process import engine_server
from benchmarks.shop_evaluation.cases import EvaluationSplit, read_split, sample_users
from benchmarks.shop_evaluation.evaluation import Evaluator, Settings, Target
from benchmarks.shop_evaluation.rankings import alone, at_random, fused, is_cold_start, most_popular
from benchmarks.shop_evaluation.signals import (
    Collection,
    UserSignals,
    collect,
    pending,
    read_signals,
)
from benchmarks.shop_evaluation.weights import WeightScore, label, search, simplex
from benchmarks.shop_recommend_benchmark import HTTP_LOGGER, LIMA, LONG_TIMEOUT_SECONDS
from minigestor_client import AsyncClient, Point, Timeouts
from shop.catalog.schema import MATERIALIZATIONS
from shop.recommend.candidates import Candidate, Signal
from shop.recommend.fusion import FusionWeights
from shop.recommend.recommender import Nearby, Recommender
from shop.settings import (
    DATASET_DIRECTORY,
    DEFAULT_CANDIDATES_PER_SIGNAL,
    DEFAULT_COPURCHASE_WEIGHT,
    DEFAULT_NEARBY_RADIUS_METERS,
    DEFAULT_NEARBY_WEIGHT,
    DEFAULT_RECOMMENDATION_SPLIT,
    DEFAULT_TEXT_WEIGHT,
    DEFAULT_USER_PROFILE_PRODUCTS,
    DEFAULT_VISUAL_WEIGHT,
)

REPORT_NAME = "tienda_evaluacion"
UNUSED_KEY = "sin-clave"
COPURCHASE_ALONE = "compras conjuntas"
CLIP_ALONE = "visual (CLIP)"
SIFT_ALONE = "visual (SIFT)"
TEXT_ALONE = "texto (BM25)"
NEARBY_ALONE = "cercanía"
DEFAULT_FUSION = "fusión, pesos por omisión"
TUNED_FUSION = "fusión, pesos ajustados"
POPULAR = "lo más vendido"
RANDOM = "al azar"
WEIGHT_SEARCH = "búsqueda de pesos"
WEIGHTS_OPERATION = "pesos en validación"
PLACE = "puesto {}"
DEFAULT_PLACE = "pesos por omisión"
TUNED_MINUS_DEFAULT = "ajustados menos por omisión"
TUNED_MINUS_COPURCHASE = "fusión ajustada menos compras conjuntas"
CLIP_FUSION = "compras + CLIP + texto"
SIFT_FUSION = "compras + SIFT + texto"
SIFT_MINUS_CLIP = "SIFT menos CLIP (fundidas)"
VISUAL_OPERATION = "prueba: parecido visual"
COLD_USERS = "prueba: usuarios con {} compras"
COLD_PRODUCTS = "prueba: productos con {} compras"
COLD_FUSION = "prueba: fusión {}"
WITHOUT_COPURCHASES = "sin compras conjuntas"
WITH_COPURCHASES = "con compras conjuntas"
SAME_RUNS = "listas iguales en las dos pasadas"
CONSISTENCY = "comprobación"
# Grupos del arranque en frío: (rótulo, mínimo, máximo) de compras de entrenamiento. El
# 5-core deja a cada usuario al menos 3 de entrenamiento; un producto de la prueba puede no
# tener ninguna (106 de 57 439).
USER_GROUPS = (("3", 3, 3), ("4", 4, 4), ("5 a 9", 5, 9), ("10 o más", 10, None))
PRODUCT_GROUPS = (
    ("0 a 4", 0, 4),
    ("5 a 19", 5, 19),
    ("20 a 99", 20, 99),
    ("100 o más", 100, None),
)
FOUR_SIGNALS = (Signal.COPURCHASE, Signal.VISUAL, Signal.TEXT, Signal.NEARBY)
THREE_SIGNALS = (Signal.COPURCHASE, Signal.VISUAL, Signal.TEXT)
TOP_WEIGHTS = 8
WEIGHT_DIGITS = 2

Group = tuple[str, int, int | None]


@dataclass(frozen=True, slots=True)
class Arguments:
    data_dir: Path
    engine_data_dir: Path
    signals: Path
    users: int
    seed: int
    k: int
    grid_steps: int
    resamples: int
    confidence: float
    output: Path


def run(arguments: Arguments) -> Report:
    split = read_split(arguments.data_dir / DATASET_DIRECTORY)
    users = sample_users(list(split.held_out), arguments.users, arguments.seed)
    report = Report(REPORT_NAME, _parameters(arguments, split, users))
    report.parameters["materializacion"] = _collect_signals(arguments, users)
    found = read_signals(arguments.signals)
    evaluator = Evaluator(
        [found[user] for user in users],
        split,
        Settings(arguments.k, arguments.resamples, arguments.confidence, arguments.seed),
    )
    _evaluate(report, evaluator, arguments)
    report.parameters["carga_media_al_terminar"] = load_average()
    return report


def _collect_signals(arguments: Arguments, users: Sequence[str]) -> dict[str, Any]:
    environment = {
        IMPORT_DIRECTORIES_VARIABLE: str(arguments.data_dir.resolve()),
        STATEMENT_TIMEOUT_VARIABLE: str(int(LONG_TIMEOUT_SECONDS)),
    }
    with engine_server(arguments.engine_data_dir, environment) as url:
        return asyncio.run(_collect(url, users, arguments))


async def _collect(url: str, users: Sequence[str], arguments: Arguments) -> dict[str, Any]:
    timeouts = Timeouts(read_seconds=LONG_TIMEOUT_SECONDS)
    async with AsyncClient(url, UNUSED_KEY, timeouts=timeouts) as engine:
        materialization = await _train_only_materialization(engine)
        missing = list(pending(users, read_signals(arguments.signals)))
        logging.info("faltan los candidatos de %d de %d usuarios", len(missing), len(users))
        recommender = Recommender(
            engine,
            DEFAULT_RECOMMENDATION_SPLIT,
            DEFAULT_CANDIDATES_PER_SIGNAL,
            DEFAULT_USER_PROFILE_PRODUCTS,
        )
        await collect(engine, recommender, missing, _collection(arguments), arguments.signals)
    return materialization


async def _train_only_materialization(engine: AsyncClient) -> dict[str, Any]:
    rows = await engine.fetch_all(f"SELECT * FROM {MATERIALIZATIONS}", [])
    if [row["particion"] for row in rows] != [DEFAULT_RECOMMENDATION_SPLIT]:
        raise SystemExit(f"las compras conjuntas no son solo de entrenamiento: {rows}")
    return dict(rows[0])


def _collection(arguments: Arguments) -> Collection:
    return Collection(
        DEFAULT_RECOMMENDATION_SPLIT,
        arguments.k,
        _default_weights(),
        Nearby(Point(LIMA["lat"], LIMA["lon"]), DEFAULT_NEARBY_RADIUS_METERS),
    )


def _evaluate(report: Report, evaluator: Evaluator, arguments: Arguments) -> None:
    k = arguments.k
    defaults = _default_weights()
    tuned_search = _search(evaluator, FOUR_SIGNALS, arguments.grid_steps, _clip)
    tuned = tuned_search[0].weights
    lists = _main_lists(evaluator, defaults, tuned, arguments.seed)
    for name, ranked in lists.items():
        for target in Target:
            report.add(name, target.value, len(evaluator.users), 0.0,
                       **evaluator.summary(ranked, target, evaluator.everyone()))
    _add_weight_search(report, evaluator, tuned_search, defaults, lists[DEFAULT_FUSION])
    report.add(TUNED_MINUS_DEFAULT, Target.TEST.value, len(evaluator.users), 0.0,
               **evaluator.difference(lists[TUNED_FUSION], lists[DEFAULT_FUSION], Target.TEST))
    report.add(TUNED_MINUS_COPURCHASE, Target.TEST.value, len(evaluator.users), 0.0,
               **evaluator.difference(lists[TUNED_FUSION], lists[COPURCHASE_ALONE],
                                      Target.TEST))
    _add_cold_start(report, evaluator, lists)
    _add_visual(report, evaluator, arguments, lists)
    report.parameters["pesos_por_omision"] = asdict(defaults)
    report.parameters["pesos_ajustados"] = asdict(tuned)
    report.parameters["k"] = k


def _main_lists(
    evaluator: Evaluator, defaults: FusionWeights, tuned: FusionWeights, seed: int
) -> dict[str, list[list[str]]]:
    k = evaluator.settings.k
    split = evaluator.split
    popular = split.most_popular()
    generator = random.Random(seed)
    return {
        COPURCHASE_ALONE: evaluator.lists(_signal_alone(Signal.COPURCHASE, k)),
        CLIP_ALONE: evaluator.lists(_signal_alone(Signal.VISUAL, k)),
        TEXT_ALONE: evaluator.lists(_signal_alone(Signal.TEXT, k)),
        NEARBY_ALONE: evaluator.lists(_signal_alone(Signal.NEARBY, k)),
        DEFAULT_FUSION: evaluator.lists(_fusion(defaults, k, _clip)),
        TUNED_FUSION: evaluator.lists(_fusion(tuned, k, _clip)),
        POPULAR: evaluator.lists(lambda _, excluded: most_popular(popular, excluded, k)),
        RANDOM: evaluator.lists(
            lambda _, excluded: at_random(split.catalog, excluded, k, generator)
        ),
    }


def _search(
    evaluator: Evaluator,
    signals: Sequence[Signal],
    steps: int,
    runs: Callable[[UserSignals], dict[Signal, tuple[Candidate, ...]]],
) -> list[WeightScore]:
    everyone = evaluator.everyone()
    k = evaluator.settings.k

    def score(weights: FusionWeights) -> tuple[float, float]:
        scores = evaluator.scores(
            evaluator.lists(_fusion(weights, k, runs)), Target.VALIDATION, everyone
        )
        return scores.mean_ndcg(), scores.mean_hit()

    grid = simplex(steps, signals)
    logging.info("buscando pesos: %d combinaciones de %d señales", len(grid), len(signals))
    return search(grid, score)


def _add_weight_search(
    report: Report,
    evaluator: Evaluator,
    found: Sequence[WeightScore],
    defaults: FusionWeights,
    default_lists: Sequence[Sequence[str]],
) -> None:
    report.add(
        WEIGHT_SEARCH,
        Target.VALIDATION.value,
        len(evaluator.users),
        0.0,
        combinaciones=len(found),
        pesos=[list(asdict(score.weights).values()) for score in found],
        ndcg=[round(score.ndcg, 5) for score in found],
        hit=[round(score.hit, 5) for score in found],
    )
    k = evaluator.settings.k
    for place, score in enumerate(found[:TOP_WEIGHTS], start=1):
        ranked = evaluator.lists(_fusion(score.weights, k, _clip))
        report.add(PLACE.format(place), WEIGHTS_OPERATION, len(evaluator.users), 0.0,
                   puesto=place, pesos=asdict(score.weights), rotulo=label(score.weights),
                   **evaluator.summary(ranked, Target.VALIDATION, evaluator.everyone()))
    report.add(DEFAULT_PLACE, WEIGHTS_OPERATION, len(evaluator.users), 0.0,
               puesto=_place_of(found, defaults), pesos=asdict(defaults),
               rotulo=f"{label(defaults)} (omisión)",
               **evaluator.summary(default_lists, Target.VALIDATION, evaluator.everyone()))


def _add_cold_start(
    report: Report, evaluator: Evaluator, lists: dict[str, list[list[str]]]
) -> None:
    split = evaluator.split
    users = evaluator.users
    groups: list[tuple[str, list[int]]] = []
    for name, low, high in USER_GROUPS:
        selected = [
            index for index, user in enumerate(users)
            if _within(split.train_purchases[user.user], low, high)
        ]
        groups.append((COLD_USERS.format(name), selected))
    for name, low, high in PRODUCT_GROUPS:
        selected = [
            index for index in evaluator.everyone()
            if _within(split.popularity[evaluator.target_of(index, Target.TEST)], low, high)
        ]
        groups.append((COLD_PRODUCTS.format(name), selected))
    cold = [is_cold_start(user.clip, frozenset(user.history)) for user in users]
    groups.append((COLD_FUSION.format(WITHOUT_COPURCHASES),
                   [index for index, flag in enumerate(cold) if flag]))
    groups.append((COLD_FUSION.format(WITH_COPURCHASES),
                   [index for index, flag in enumerate(cold) if not flag]))
    for operation, selected in groups:
        if not selected:
            continue
        for name, ranked in lists.items():
            report.add(name, operation, len(selected), 0.0,
                       **evaluator.summary(ranked, Target.TEST, selected))


def _add_visual(
    report: Report,
    evaluator: Evaluator,
    arguments: Arguments,
    lists: dict[str, list[list[str]]],
) -> None:
    k = arguments.k
    sift_alone = evaluator.lists(
        lambda user, excluded: alone(user.sift.get(Signal.VISUAL, ()), excluded, k)
    )
    clip_tuned = _search(evaluator, THREE_SIGNALS, arguments.grid_steps, _clip_without_nearby)
    sift_tuned = _search(evaluator, THREE_SIGNALS, arguments.grid_steps, _sift)
    clip_fused = evaluator.lists(_fusion(clip_tuned[0].weights, k, _clip_without_nearby))
    sift_fused = evaluator.lists(_fusion(sift_tuned[0].weights, k, _sift))
    everyone = evaluator.everyone()
    measured = (
        (CLIP_ALONE, lists[CLIP_ALONE], None),
        (SIFT_ALONE, sift_alone, None),
        (CLIP_FUSION, clip_fused, clip_tuned[0].weights),
        (SIFT_FUSION, sift_fused, sift_tuned[0].weights),
    )
    for name, ranked, weights in measured:
        extra = {"pesos": asdict(weights)} if weights is not None else {}
        report.add(name, VISUAL_OPERATION, len(everyone), 0.0,
                   **evaluator.summary(ranked, Target.TEST, everyone), **extra)
    report.add(SIFT_MINUS_CLIP, VISUAL_OPERATION, len(everyone), 0.0,
               **evaluator.difference(sift_fused, clip_fused, Target.TEST))
    report.add(SAME_RUNS, CONSISTENCY, len(everyone), 0.0, **_same_runs(evaluator.users))


def _same_runs(users: Sequence[UserSignals]) -> dict[str, int]:
    """Las compras conjuntas y el texto no dependen del parecido visual: las dos pasadas
    tienen que haber traído las mismas listas."""
    return {
        signal.value: sum(
            1 for user in users if user.clip.get(signal) == user.sift.get(signal)
        )
        for signal in (Signal.COPURCHASE, Signal.TEXT)
    }


def _signal_alone(signal: Signal, k: int) -> Callable[[UserSignals, frozenset[str]], list[str]]:
    return lambda user, excluded: alone(user.clip.get(signal, ()), excluded, k)


def _fusion(
    weights: FusionWeights,
    k: int,
    runs: Callable[[UserSignals], dict[Signal, tuple[Candidate, ...]]],
) -> Callable[[UserSignals, frozenset[str]], list[str]]:
    return lambda user, excluded: fused(runs(user), weights, excluded, k)


def _clip(user: UserSignals) -> dict[Signal, tuple[Candidate, ...]]:
    return user.clip


def _clip_without_nearby(user: UserSignals) -> dict[Signal, tuple[Candidate, ...]]:
    return {signal: found for signal, found in user.clip.items() if signal is not Signal.NEARBY}


def _sift(user: UserSignals) -> dict[Signal, tuple[Candidate, ...]]:
    return user.sift


def _within(value: int, low: int, high: int | None) -> bool:
    return value >= low and (high is None or value <= high)


def _place_of(found: Sequence[WeightScore], weights: FusionWeights) -> int | None:
    rounded = _rounded(weights)
    for place, score in enumerate(found, start=1):
        if _rounded(score.weights) == rounded:
            return place
    return None


def _rounded(weights: FusionWeights) -> FusionWeights:
    total = weights.copurchase + weights.visual + weights.text + weights.nearby
    return FusionWeights(
        *(round(value / total, WEIGHT_DIGITS) for value in asdict(weights).values())
    )


def _default_weights() -> FusionWeights:
    return FusionWeights(
        DEFAULT_COPURCHASE_WEIGHT, DEFAULT_VISUAL_WEIGHT, DEFAULT_TEXT_WEIGHT, DEFAULT_NEARBY_WEIGHT
    )


def _parameters(
    arguments: Arguments, split: EvaluationSplit, users: Sequence[str]
) -> dict[str, Any]:
    return {
        "gestor": "uvicorn en otro proceso, con el catálogo y las compras conjuntas cargados",
        "particion": "last_out: validación = penúltima compra, prueba = última",
        "usuarios_en_el_dataset": len(split.held_out),
        "usuarios_evaluados": len(users),
        "productos": len(split.catalog),
        "semilla": arguments.seed,
        "k": arguments.k,
        "pasos_de_la_rejilla": arguments.grid_steps,
        "remuestreos_bootstrap": arguments.resamples,
        "confianza": arguments.confidence,
        "candidatos_por_senal": DEFAULT_CANDIDATES_PER_SIGNAL,
        "compras_que_describen_al_usuario": DEFAULT_USER_PROFILE_PRODUCTS,
        "ubicacion_de_la_cercania": LIMA,
        "radio_de_la_cercania_m": DEFAULT_NEARBY_RADIUS_METERS,
        "maquina": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--engine-data-dir", type=Path, required=True)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--users", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=82)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--grid-steps", type=int, default=10)
    parser.add_argument("--resamples", type=int, default=1000)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    parsed = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger(HTTP_LOGGER).setLevel(logging.WARNING)
    report = run(Arguments(**vars(parsed)))
    print(report.render())
    print(f"resultados en {report.save(parsed.output)}")


if __name__ == "__main__":
    main()
