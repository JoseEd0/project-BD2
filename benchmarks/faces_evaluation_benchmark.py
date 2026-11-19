"""Rostros: verificación con el protocolo de LFW, umbrales a un FAR fijo, identificación con
galería y sondas en el gestor (HNSW contra búsqueda exacta) y latencia de cada etapa.

    .venv/bin/python -m faces lfw --salida data/datasets/lfw
    .venv/bin/python -m faces models --salida data/models/faces
    .venv/bin/python -m benchmarks.faces_evaluation_benchmark --lfw data/datasets/lfw \\
        --models data/models/faces --fars 0.01 0.001 0.0001 0.00001 \\
        --thresholds 0.363 0.45 0.5 --gallery-size 1000 --target-fpir 0.01

Cada foto de LFW se describe una vez con el pipeline del servicio, tomando el rostro más
cercano al centro (ver `benchmarks.faces_evaluation.embeddings`). Con esos embeddings:

1. **Verificación** con `pairs.txt`: 10 pliegues de 300 pares de la misma persona y 300 de
   personas distintas. El umbral de cada pliegue se elige con los otros nueve y se mide con
   él; se reporta la media y la desviación estándar de las diez exactitudes. Un par con una
   foto sin rostro cuenta con el coseno más bajo, -1: se rechaza siempre.
2. **TAR a un FAR fijo** y la curva ROC, con los 6 000 pares del protocolo y con todos los
   pares de LFW: los de la misma persona y los de la primera foto de cada persona contra la
   de cada otra (16,5 millones), que miden FAR de hasta 10⁻⁷ que 3 000 pares no alcanzan.
3. **Identificación** en el gestor: una tabla `VECTOR(128)` con la primera foto de cada
   persona con al menos dos, un HNSW coseno y `SIMILAR_TO(..., k = 1)` por SQL para cada
   otra foto de esas personas (rank-1) y para la única foto de las demás (conjunto
   abierto). La misma consulta con la métrica `dot`, que el índice no ordena, es la
   búsqueda exacta del gestor (con norma 1, ordena igual que el coseno); numpy da la
   referencia.
4. **Umbral para N personas:** un desconocido se compara con toda la galería, así que con
   N personas y una tasa `--target-fpir` de desconocidos aceptados cada comparación debe
   aceptar a lo sumo `FPIR / N` impostores: es el umbral a ese FAR con todos los pares.

`--embeddings-cache` guarda los embeddings y los tiempos de cada etapa en un `.npz` y los
reutiliza en la siguiente ejecución.
"""

from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import faiss
import numpy as np
import numpy.typing as npt

from benchmarks.common import Report, load_average, machine_description, results_directory
from benchmarks.faces_evaluation.embeddings import (
    STAGES,
    LfwEmbeddings,
    compute_embeddings,
    load_embeddings,
)
from benchmarks.faces_evaluation.metrics import (
    OperatingPoint,
    Scores,
    cross_validate,
    open_set_errors,
    operating_point,
    rank_one,
    roc_curve,
    tar_at_far,
    threshold_at_far,
)
from config import EngineConfig
from faces.lfw import IMAGES_DIRECTORY, Fold, lfw_images, read_pairs
from query.engine import Engine
from query.plan import MILLISECONDS_PER_SECOND

REPORT_NAME = "rostros_evaluacion"
PAIRS_FILE = "pairs.txt"
MODEL = "SFace"
PROTOCOL = "pares del protocolo"
ALL_PAIRS = "todos los pares"
FOLDS = "pliegues"
ACCURACY = "exactitud"
TAR_AT_FAR = "TAR a FAR"
ROC = "ROC"
OPERATING_POINT = "punto de operación"
RECOMMENDATION = "umbral recomendado"
IDENTIFICATION = "identificación"
OPEN_SET = "conjunto abierto"
OPEN_SET_CURVE = "conjunto abierto por umbral"
LATENCY = "latencia por etapa"
DETECTION = "detección en LFW"
SQL_HNSW = "HNSW (gestor)"
SQL_EXACT = "exacta (gestor)"
NUMPY_EXACT = "exacta (numpy)"
SEARCH_STAGE = "buscar (HNSW del gestor)"
TOTAL_STAGE = "total"
TABLE = "galeria"
EXACT_TAIL = "USING HNSW WITH METRIC = dot"
NEAREST = (
    f"SELECT persona, SIMILARITY_SCORE() AS score FROM {TABLE} "
    "WHERE SIMILAR_TO(embedding, $1, k = 1)"
)
# Un par con una foto sin rostro se rechaza siempre: su coseno es el mínimo posible.
MISSING_FACE_SCORE = -1.0
MIN_GALLERY_PHOTOS = 2
PERCENTILES = (50, 95, 99)
ROC_THRESHOLDS = tuple(round(value, 4) for value in np.arange(-0.1, 0.9, 0.005))
OPEN_SET_THRESHOLDS = tuple(round(value, 3) for value in np.arange(0.25, 0.701, 0.01))
DEFAULT_FARS = (0.01, 0.001, 0.0001, 0.00001)
DEFAULT_THRESHOLDS = (0.363, 0.45, 0.5)
DEFAULT_GALLERY_SIZE = 1000
DEFAULT_TARGET_FPIR = 0.01
DEFAULT_WORKERS = 1
DEFAULT_THREADS = 1
DIGITS = 4
TIME_DIGITS = 3


@dataclass(frozen=True, slots=True)
class PairScores:
    """Los cosenos de unos pares, si son de la misma persona y su pliegue."""

    scores: Scores
    same: npt.NDArray[np.bool_]
    folds: npt.NDArray[np.int_]
    missing: int

    @property
    def genuine(self) -> Scores:
        return self.scores[self.same]

    @property
    def impostor(self) -> Scores:
        return self.scores[~self.same]


@dataclass(frozen=True, slots=True)
class SearchResult:
    """La persona del primer vecino de cada sonda, su coseno y lo que tardó cada búsqueda."""

    people: npt.NDArray[np.int_]
    scores: Scores
    milliseconds: list[float]


def protocol_scores(embeddings: LfwEmbeddings, folds: Sequence[Fold]) -> PairScores:
    scores, same, fold_of, missing = [], [], [], 0
    for number, fold in enumerate(folds, start=1):
        for pair in (*fold.matched, *fold.mismatched):
            first, second = (embeddings.row_of(ref) for ref in (pair.first, pair.second))
            if embeddings.found[first] and embeddings.found[second]:
                scores.append(float(embeddings.vectors[first] @ embeddings.vectors[second]))
            else:
                scores.append(MISSING_FACE_SCORE)
                missing += 1
            same.append(pair.same_person)
            fold_of.append(number)
    return PairScores(np.array(scores), np.array(same), np.array(fold_of), missing)


def all_pair_scores(embeddings: LfwEmbeddings) -> tuple[Scores, Scores]:
    """Los cosenos de todos los pares de fotos de una misma persona, y los de la primera
    foto de cada persona contra la de cada otra."""
    rows_by_person = _rows_by_person(embeddings)
    genuine = [
        _upper_triangle(embeddings.vectors[rows])
        for rows in rows_by_person.values()
        if len(rows) >= MIN_GALLERY_PHOTOS
    ]
    firsts = embeddings.vectors[[rows[0] for rows in rows_by_person.values()]]
    return np.concatenate(genuine), _upper_triangle(firsts)


def _upper_triangle(vectors: npt.NDArray[np.float32]) -> Scores:
    similarities = vectors @ vectors.T
    return similarities[np.triu_indices(len(vectors), k=1)].astype(np.float64)


def _rows_by_person(embeddings: LfwEmbeddings) -> dict[str, list[int]]:
    rows: dict[str, list[int]] = {}
    for row, person in enumerate(embeddings.people):
        if embeddings.found[row]:
            rows.setdefault(person, []).append(row)
    return rows


def add_verification(report: Report, pairs: PairScores) -> list[float]:
    """Los pliegues y la exactitud media; devuelve el umbral elegido en cada pliegue."""
    results = cross_validate(pairs.scores, pairs.same, pairs.folds)
    accuracies = [result.test_accuracy for result in results]
    thresholds = [result.threshold for result in results]
    report.add(
        MODEL,
        FOLDS,
        len(pairs.scores),
        0.0,
        pliegue=[result.fold for result in results],
        exactitud=[round(value, DIGITS) for value in accuracies],
        exactitud_entrenamiento=[round(result.train_accuracy, DIGITS) for result in results],
        umbral=[round(value, DIGITS) for value in thresholds],
    )
    report.add(
        MODEL,
        ACCURACY,
        len(pairs.scores),
        0.0,
        media=round(statistics.fmean(accuracies), DIGITS),
        desviacion=round(statistics.pstdev(accuracies), DIGITS),
        umbral_medio=round(statistics.fmean(thresholds), DIGITS),
        umbral_min=round(min(thresholds), DIGITS),
        umbral_max=round(max(thresholds), DIGITS),
        pares_sin_rostro=pairs.missing,
    )
    return thresholds


def add_operating_points(
    report: Report,
    source: str,
    genuine: Scores,
    impostor: Scores,
    arguments: argparse.Namespace,
    fold_thresholds: Sequence[float],
) -> None:
    for far in arguments.fars:
        if far * impostor.size < 1:
            continue
        _add_point(report, source, TAR_AT_FAR, f"FAR {far:g}", tar_at_far(genuine, impostor, far))
    chosen = {f"umbral {t:g}": t for t in arguments.thresholds}
    chosen["umbral de los pliegues"] = statistics.fmean(fold_thresholds)
    for label, threshold in chosen.items():
        _add_point(
            report, source, OPERATING_POINT, label, operating_point(genuine, impostor, threshold)
        )
    curve = roc_curve(genuine, impostor, ROC_THRESHOLDS)
    report.add(
        source,
        ROC,
        impostor.size,
        0.0,
        umbral=[point.threshold for point in curve],
        tar=[round(point.tar, 6) for point in curve],
        far=[point.far for point in curve],
        genuinos=int(genuine.size),
        impostores=int(impostor.size),
    )


def _add_point(
    report: Report, source: str, operation: str, label: str, point: OperatingPoint
) -> None:
    report.add(
        f"{source}: {label}",
        operation,
        point.impostors,
        0.0,
        fuente=source,
        umbral=round(point.threshold, DIGITS),
        tar=round(point.tar, DIGITS),
        far=point.far,
        impostores_aceptados=point.accepted_impostors,
    )


class GalleryTable:
    """Una galería de una foto por persona en un gestor propio, con su HNSW coseno."""

    def __init__(self, vectors: npt.NDArray[np.float32], directory: Path) -> None:
        self._engine = Engine(EngineConfig(data_directory=directory))
        self._engine.execute(
            f"CREATE TABLE {TABLE} (id INT PRIMARY KEY, persona INT, "
            f"embedding VECTOR({vectors.shape[1]}))"
        )
        table = self._engine.table(TABLE)
        for person, vector in enumerate(vectors):
            table.insert((person, person, tuple(vector.tolist())))
        started = time.perf_counter()
        self._engine.execute(
            f"CREATE INDEX {TABLE}_hnsw ON {TABLE} USING HNSW (embedding) WITH (metric = 'cosine')"
        )
        self.build_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND

    def search(self, probes: npt.NDArray[np.float32], tail: str) -> SearchResult:
        people, scores, milliseconds = [], [], []
        statement = f"{NEAREST} {tail}".strip()
        for probe in probes:
            started = time.perf_counter()
            rows = self._engine.execute(statement, parameters=[probe.tolist()]).rows
            milliseconds.append((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
            person, score = rows[0]
            people.append(int(str(person)))
            scores.append(float(str(score)))
        return SearchResult(np.array(people), np.array(scores), milliseconds)

    def close(self) -> None:
        self._engine.close()


def numpy_search(gallery: npt.NDArray[np.float32], probes: npt.NDArray[np.float32]) -> SearchResult:
    started = time.perf_counter()
    similarities = probes.astype(np.float64) @ gallery.astype(np.float64).T
    best = np.argmax(similarities, axis=1)
    elapsed = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    return SearchResult(
        best, similarities[np.arange(len(probes)), best], [elapsed / len(probes)] * len(probes)
    )


@dataclass(frozen=True, slots=True)
class IdentificationSplit:
    """La galería (una foto por persona), las sondas de esas personas con su persona, y
    las sondas de personas que no están."""

    gallery: npt.NDArray[np.float32]
    probes: npt.NDArray[np.float32]
    probe_people: npt.NDArray[np.int_]
    strangers: npt.NDArray[np.float32]


def identification_split(embeddings: LfwEmbeddings) -> IdentificationSplit:
    rows_by_person = _rows_by_person(embeddings)
    gallery_rows: list[int] = []
    probe_rows: list[int] = []
    probe_people: list[int] = []
    stranger_rows: list[int] = []
    for rows in rows_by_person.values():
        if len(rows) < MIN_GALLERY_PHOTOS:
            stranger_rows.append(rows[0])
            continue
        probe_people += [len(gallery_rows)] * (len(rows) - 1)
        gallery_rows.append(rows[0])
        probe_rows += rows[1:]
    vectors = embeddings.vectors
    return IdentificationSplit(
        vectors[gallery_rows], vectors[probe_rows], np.array(probe_people), vectors[stranger_rows]
    )


def add_identification(
    report: Report, split: IdentificationSplit, arguments: argparse.Namespace
) -> list[float]:
    """Rank-1 y conjunto abierto por cada búsqueda; devuelve los tiempos del HNSW."""
    with tempfile.TemporaryDirectory() as directory:
        table = GalleryTable(split.gallery, Path(directory))
        try:
            results = {
                SQL_HNSW: (table.search(split.probes, ""), table.search(split.strangers, "")),
                SQL_EXACT: (
                    table.search(split.probes, EXACT_TAIL),
                    table.search(split.strangers, EXACT_TAIL),
                ),
            }
        finally:
            table.close()
    results[NUMPY_EXACT] = (
        numpy_search(split.gallery, split.probes),
        numpy_search(split.gallery, split.strangers),
    )
    reference = results[NUMPY_EXACT][0].people
    gallery_size = len(split.gallery)
    for technique, (known, strangers) in results.items():
        correct = known.people == split.probe_people
        report.add(
            technique,
            IDENTIFICATION,
            gallery_size,
            _percentile(known.milliseconds, PERCENTILES[0]),
            rank_1=round(rank_one(list(known.people), list(split.probe_people)), DIGITS),
            acuerdo_con_exacta=round(float(np.mean(known.people == reference)), DIGITS),
            sondas=len(known.people),
            desconocidas=len(strangers.people),
            construccion_ms=round(table.build_ms, TIME_DIGITS) if technique == SQL_HNSW else 0.0,
            **_latency_fields(known.milliseconds + strangers.milliseconds),
        )
        _add_open_set(
            report, technique, gallery_size, correct, known.scores, strangers.scores, arguments
        )
    return results[SQL_HNSW][0].milliseconds + results[SQL_HNSW][1].milliseconds


def _add_open_set(
    report: Report,
    technique: str,
    gallery_size: int,
    correct: npt.NDArray[np.bool_],
    known: Scores,
    strangers: Scores,
    arguments: argparse.Namespace,
) -> None:
    curve = [open_set_errors(correct, known, strangers, t) for t in OPEN_SET_THRESHOLDS]
    report.add(
        technique,
        OPEN_SET_CURVE,
        gallery_size,
        0.0,
        umbral=list(OPEN_SET_THRESHOLDS),
        fpir=[round(errors.fpir, DIGITS) for errors in curve],
        fnir=[round(errors.fnir, DIGITS) for errors in curve],
    )
    for threshold in arguments.thresholds:
        errors = open_set_errors(correct, known, strangers, threshold)
        report.add(
            f"{technique}: umbral {threshold:g}",
            OPEN_SET,
            gallery_size,
            0.0,
            umbral=threshold,
            fpir=round(errors.fpir, DIGITS),
            fnir=round(errors.fnir, DIGITS),
        )


def add_recommendations(
    report: Report,
    pairs: PairScores,
    all_impostors: Scores,
    fold_thresholds: Sequence[float],
    arguments: argparse.Namespace,
) -> None:
    per_comparison = arguments.target_fpir / arguments.gallery_size
    recommended = {
        "1:1, exactitud de los pliegues": statistics.fmean(fold_thresholds),
        "1:1, FAR 0.001 en el protocolo": threshold_at_far(pairs.impostor, 0.001),
        f"1:N con {arguments.gallery_size} personas, FPIR {arguments.target_fpir:g}": (
            threshold_at_far(all_impostors, per_comparison)
        ),
    }
    for label, threshold in recommended.items():
        report.add(
            label, RECOMMENDATION, arguments.gallery_size, 0.0, umbral=round(threshold, DIGITS)
        )


def add_latencies(report: Report, embeddings: LfwEmbeddings, search_ms: Sequence[float]) -> None:
    with_face = embeddings.milliseconds[embeddings.found]
    stages = {stage: with_face[:, column] for column, stage in enumerate(STAGES)}
    stages[SEARCH_STAGE] = np.array(search_ms)
    stages[TOTAL_STAGE] = with_face.sum(axis=1)
    for stage, values in stages.items():
        report.add(
            stage,
            LATENCY,
            len(values),
            _percentile(list(values), PERCENTILES[0]),
            **_latency_fields(list(values)),
        )
    report.add(
        "YuNet",
        DETECTION,
        len(embeddings.found),
        0.0,
        con_rostro=int(embeddings.found.sum()),
        con_varios=int(np.count_nonzero(embeddings.faces > 1)),
    )


def _latency_fields(values: Sequence[float]) -> dict[str, float]:
    fields = {f"p{p}_ms": _percentile(values, p) for p in PERCENTILES}
    fields["p95_sobre_p50_ms"] = round(fields["p95_ms"] - fields["p50_ms"], TIME_DIGITS)
    fields["p99_sobre_p95_ms"] = round(fields["p99_ms"] - fields["p95_ms"], TIME_DIGITS)
    return fields


def _percentile(values: Sequence[float], percentile: int) -> float:
    return round(float(np.percentile(values, percentile)), TIME_DIGITS)


def embeddings_for(arguments: argparse.Namespace) -> LfwEmbeddings:
    cache: Path | None = arguments.embeddings_cache
    if cache is not None and cache.is_file():
        print(f"embeddings de {cache}")
        return load_embeddings(cache)
    images = lfw_images(arguments.lfw / IMAGES_DIRECTORY)
    embeddings = compute_embeddings(images, arguments.models, arguments.workers, arguments.threads)
    if cache is not None:
        embeddings.save(cache)
    return embeddings


def run(arguments: argparse.Namespace) -> Report:
    cv2.setNumThreads(arguments.threads)
    faiss.omp_set_num_threads(arguments.threads)
    report = Report(REPORT_NAME, _parameters(arguments))
    embeddings = embeddings_for(arguments)
    print(f"{int(embeddings.found.sum())} de {len(embeddings.names)} fotos con rostro")
    pairs = protocol_scores(embeddings, read_pairs(arguments.lfw / PAIRS_FILE))
    fold_thresholds = add_verification(report, pairs)
    add_operating_points(
        report, PROTOCOL, pairs.genuine, pairs.impostor, arguments, fold_thresholds
    )
    all_genuine, all_impostors = all_pair_scores(embeddings)
    add_operating_points(report, ALL_PAIRS, all_genuine, all_impostors, arguments, fold_thresholds)
    add_recommendations(report, pairs, all_impostors, fold_thresholds, arguments)
    print("verificación medida")
    search_ms = add_identification(report, identification_split(embeddings), arguments)
    add_latencies(report, embeddings, search_ms)
    report.parameters["carga_media_al_terminar"] = load_average()
    return report


def _parameters(arguments: argparse.Namespace) -> dict[str, Any]:
    return {
        "fars": list(arguments.fars),
        "umbrales": list(arguments.thresholds),
        "personas_en_la_galeria_objetivo": arguments.gallery_size,
        "fpir_objetivo": arguments.target_fpir,
        "procesos": arguments.workers,
        "hilos_opencv_y_faiss": arguments.threads,
        "embeddings_de_cache": bool(
            arguments.embeddings_cache and arguments.embeddings_cache.is_file()
        ),
        "rostro_elegido": "el más cercano al centro de la foto",
        "opencv": cv2.__version__,
        "equipo": machine_description(),
    }


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--lfw", type=Path, required=True, help="carpeta con lfw/ y pairs.txt")
    parser.add_argument("--models", type=Path, required=True, help="carpeta de YuNet y SFace")
    parser.add_argument("--fars", type=float, nargs="+", default=list(DEFAULT_FARS))
    parser.add_argument("--thresholds", type=float, nargs="+", default=list(DEFAULT_THRESHOLDS))
    parser.add_argument("--gallery-size", type=int, default=DEFAULT_GALLERY_SIZE)
    parser.add_argument("--target-fpir", type=float, default=DEFAULT_TARGET_FPIR)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--embeddings-cache", type=Path, help="un .npz que se crea o se reutiliza")
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    arguments = parse_arguments(argv)
    report = run(arguments)
    print(report.render())
    print(f"\nResultados crudos en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
