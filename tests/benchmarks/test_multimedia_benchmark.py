"""El experimento multimedia, en pequeño: fotos y audios generados en el test.

No mide nada: comprueba que las cuentas de Recall@k y de la precisión por clase son las
del libro, y que el experimento corre de punta a punta, guarda sus resultados y compara
todas las técnicas sobre los mismos vectores. Sin `BENCH_POSTGRES_DSN`, pgvector se omite.
"""

import csv
import importlib.util
import json
import os
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from benchmarks.common import POSTGRES_DSN_VARIABLE
from benchmarks.multimedia.collection import (
    MissingCollectionError,
    audio_dataset,
    images_dataset,
)
from benchmarks.multimedia.features import StaleFeaturesError, collection_features
from benchmarks.multimedia.names import (
    AUDIO,
    BUILD,
    CREATE_INDEX,
    CURVE,
    ENGINE_HNSW,
    ENGINE_IVF,
    EXACT,
    EXTRACTION_STAGE,
    FAISS_HNSW,
    FAISS_HNSW_WITHOUT_ZEROS,
    FAISS_IVF,
    FEATURES,
    GALLERY,
    IMAGES,
    MEMORY,
    PGVECTOR_HNSW,
    PGVECTOR_IVF,
    QUERY,
    RANDOM_NEIGHBOURS,
    SQL_HNSW,
    SQL_IVF,
    USEARCH_HNSW,
    dataset_operation,
    metric_operation,
)
from benchmarks.multimedia.pgvector import vector_literal
from benchmarks.multimedia.quality import (
    GroundTruth,
    class_precision_at_k,
    gallery_cases,
    random_class_precision,
    recall_at_k,
)
from benchmarks.multimedia.subsets import QUERIES_CSV, SUBSETS_DIRECTORY
from benchmarks.multimedia.techniques import SearchEffort, TechniqueSpec
from benchmarks.multimedia_benchmark import Settings, efforts_of, run, timed_queries
from tests.multimedia.media_support import SAMPLE_RATE, TONE_AMPLITUDE, write_audio, write_image

from config import EngineConfig
from index.vector import VectorMetric

SIZE = 24
QUERIES = 4
K = 3
VOCABULARY = 8
FAMILIES = ("liso", "rugoso")
FAMILY_BLURS = (6.0, 1.5)
SIDE = 160
TONES_HZ = (300.0, 2_500.0)
NOISE_LEVEL = 0.05
SEED = 3
USEARCH = importlib.util.find_spec("usearch") is not None


def test_recall_counts_the_found_that_reach_the_kth_exact_score():
    assert recall_at_k([0.9, 0.8, 0.7], kth_score=0.7, k=3) == 1.0
    assert recall_at_k([0.9, 0.5, 0.7], kth_score=0.7, k=3) == pytest.approx(2 / 3)


def test_a_tie_with_the_kth_neighbour_counts_as_found():
    assert recall_at_k([0.7 * (1 - 1e-7)], kth_score=0.7, k=1) == 1.0
    assert recall_at_k([0.69], kth_score=0.7, k=1) == 0.0


def test_missing_neighbours_count_as_misses():
    assert recall_at_k([0.9], kth_score=0.5, k=4) == 0.25
    assert recall_at_k([], kth_score=0.5, k=4) == 0.0


def test_the_class_precision_is_over_k():
    assert class_precision_at_k(["a", "b", "a"], "a", 3) == pytest.approx(2 / 3)
    assert class_precision_at_k(["a"], "a", 4) == 0.25


def test_random_neighbours_share_the_class_in_its_proportion():
    labels = ["a"] * 3 + ["b"]
    assert random_class_precision(labels, ["a", "b"]) == pytest.approx((0.75 + 0.25) / 2)


def test_the_ground_truth_finds_the_exact_neighbours_and_their_duplicates():
    vectors = np.array([[1, 0], [1, 0], [0, 1], [0.9, 0.1]], dtype=np.float32)
    truth = GroundTruth(vectors, np.array([[1, 0]], dtype=np.float32), VectorMetric.COSINE, 2)

    assert truth.recall(0, [0, 1]) == 1.0
    assert truth.recall(0, [1, 0]) == 1.0
    assert truth.recall(0, [0, 3]) == 0.5
    assert truth.recall(0, [2, 3]) == 0.0


def test_any_of_two_identical_vectors_is_the_nearest():
    vectors = np.array([[1, 0], [1, 0], [0, 1]], dtype=np.float32)
    truth = GroundTruth(vectors, np.array([[1, 0]], dtype=np.float32), VectorMetric.EUCLIDEAN, 1)

    assert truth.recall(0, [0]) == truth.recall(0, [1]) == 1.0
    assert truth.recall(0, [2]) == 0.0


def test_the_gallery_takes_the_best_and_the_worst_queries():
    found = [[0, 1], [2, 3], [4, 5], [6, 7]]
    precisions = [0.5, 1.0, 0.0, 0.5]
    labels = ["a", "b", "c", "d"]

    successes, errors = gallery_cases(found, precisions, labels, cases=1, neighbours=1, seed=SEED)

    assert [(case.query, case.neighbours) for case in successes] == [(1, (2,))]
    assert [(case.query, case.precision) for case in errors] == [(2, 0.0)]


def test_the_gallery_takes_one_query_per_class():
    found = [[0], [1], [2], [3]]
    precisions = [1.0, 0.9, 0.8, 0.0]
    labels = ["funda", "funda", "zapato", "funda"]

    successes, _ = gallery_cases(found, precisions, labels, cases=2, neighbours=1, seed=SEED)

    assert [case.query for case in successes] == [0, 2]


def test_the_sweep_keeps_the_efforts_the_index_admits_and_its_default():
    settings = Settings(sizes=(SIZE,), metrics=(VectorMetric.COSINE,), ivf_probes=(1, 8, 64))
    ivf = TechniqueSpec(
        "ivf", build=None, effort=SearchEffort.PROBES, default_effort=3, max_effort=10
    )  # type: ignore[arg-type]
    exact = TechniqueSpec("exacta", build=None)  # type: ignore[arg-type]

    assert efforts_of(ivf, settings) == [1, 3, 8]
    assert efforts_of(exact, settings) == [None]


def test_each_query_keeps_the_median_of_its_repetitions_and_the_first_answer():
    calls: list[int] = []

    def search(position: int) -> list[int]:
        calls.append(position)
        return [position, len(calls)]

    measured = timed_queries(search, queries=2, repetitions=3)

    assert calls == [0, 1, 0, 1, 0, 1]
    assert measured.found == [[0, 1], [1, 2]]
    assert len(measured.milliseconds) == 2
    assert measured.tail >= measured.median > 0


def test_a_vector_literal_keeps_every_float32_bit():
    vector = np.random.default_rng(SEED).standard_normal(16).astype(np.float32)
    parsed = np.array(vector_literal(vector)[1:-1].split(","), dtype=np.float32)

    assert np.array_equal(parsed, vector)


def test_a_missing_subset_says_how_to_download_it(tmp_path: Path):
    with pytest.raises(MissingCollectionError, match=r"benchmarks\.multimedia\.abo"):
        images_dataset(tmp_path).base(SIZE)
    with pytest.raises(MissingCollectionError, match=r"benchmarks\.multimedia\.speech_commands"):
        audio_dataset(tmp_path).queries(None)


def _photo(directory: Path, family: int, number: int) -> str:
    noise = np.random.default_rng(SEED + 100 * family + number).integers(0, 256, (SIDE, SIDE))
    blurred = cv2.GaussianBlur(noise.astype(np.uint8), (0, 0), FAMILY_BLURS[family])
    pixels = cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX)
    return str(write_image(directory / f"f{family}_{number}.png", pixels))


def _clip(directory: Path, family: int, number: int) -> str:
    times = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    noise = np.random.default_rng(SEED + 100 * family + number).standard_normal(SAMPLE_RATE)
    samples = TONE_AMPLITUDE * np.sin(2 * np.pi * TONES_HZ[family] * times) + NOISE_LEVEL * noise
    return str(write_audio(directory / f"t{family}_{number}.wav", samples.astype(np.float32)))


def _write_dataset(directory: Path, csv_name: str, label: str, make) -> None:
    media = directory / "medios"
    media.mkdir(parents=True)
    rows = [
        (make(media, number % 2, number), FAMILIES[number % 2]) for number in range(SIZE + QUERIES)
    ]
    header = ["id", "ruta", "titulo", label]
    subset = directory / SUBSETS_DIRECTORY / str(SIZE) / csv_name
    subset.parent.mkdir(parents=True)
    for path, chosen in ((subset, rows[:SIZE]), (directory / QUERIES_CSV, rows[SIZE:])):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            for number, (file, family) in enumerate(chosen, start=1):
                writer.writerow([number, file, f"{family} {number}", family])


@pytest.fixture(scope="module")
def datasets(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("multimedia")
    _write_dataset(root / "abo", "imagenes.csv", "categoria", _photo)
    _write_dataset(root / "speech_commands", "clips.csv", "palabra", _clip)
    return [images_dataset(root / "abo"), audio_dataset(root / "speech_commands")]


def _settings(features: Path) -> Settings:
    return Settings(
        sizes=(SIZE,),
        metrics=(VectorMetric.EUCLIDEAN, VectorMetric.COSINE),
        k=K,
        repetitions=2,
        build_repetitions=2,
        ivf_probes=(1, 2),
        ef_search=(4, 8),
        sql_sizes=(SIZE,),
        sql_metrics=(VectorMetric.COSINE, VectorMetric.EUCLIDEAN),
        gallery_cases=1,
        gallery_neighbours=2,
        usearch=USEARCH,
        postgres_dsn=os.environ.get(POSTGRES_DSN_VARIABLE),
        features_directory=features,
        engine=EngineConfig(vocabulary_size=VOCABULARY, feature_workers=1),
    )


@pytest.fixture(scope="module")
def report(datasets, tmp_path_factory: pytest.TempPathFactory):
    workspace = tmp_path_factory.mktemp("trabajo")
    return run(datasets, _settings(workspace / "caracteristicas"), workspace)


def _measured(report, operation: str) -> dict[str, dict]:
    return {
        item.technique: item.extra | {"ms": item.milliseconds}
        for item in report.measurements
        if item.operation == operation
    }


def test_every_technique_is_built_and_answers_with_every_metric(report):
    expected = {EXACT, ENGINE_IVF, ENGINE_HNSW, FAISS_IVF, FAISS_HNSW, FAISS_HNSW_WITHOUT_ZEROS}
    expected |= {USEARCH_HNSW} if USEARCH else set()
    expected |= {PGVECTOR_IVF, PGVECTOR_HNSW} if os.environ.get(POSTGRES_DSN_VARIABLE) else set()
    for dataset in (IMAGES, AUDIO):
        for metric in (VectorMetric.EUCLIDEAN, VectorMetric.COSINE):
            built = _measured(report, metric_operation(dataset, metric, BUILD))
            answered = _measured(report, metric_operation(dataset, metric, QUERY))
            assert expected <= built.keys()
            assert expected | {RANDOM_NEIGHBOURS} <= answered.keys()
            assert len(built[ENGINE_HNSW]["construcciones_ms"]) == 2
            assert answered[EXACT]["recall_en_k"] == 1.0
            assert all(0.0 <= answered[name]["recall_en_k"] <= 1.0 for name in expected)
            assert len(answered[ENGINE_HNSW]["por_consulta_ms"]) == QUERIES


def test_the_engine_and_faiss_directly_find_the_same_neighbours(report):
    answered = _measured(report, metric_operation(IMAGES, VectorMetric.COSINE, QUERY))
    for engine, library in ((ENGINE_IVF, FAISS_IVF), (ENGINE_HNSW, FAISS_HNSW)):
        assert answered[engine]["recall_en_k"] == answered[library]["recall_en_k"]
        assert answered[engine]["precision_categoria"] == answered[library]["precision_categoria"]


def test_through_sql_the_engine_answers_with_the_same_vectors(report):
    """`CREATE INDEX` extrae por su cuenta: si sus vectores fueran otros, su recall contra
    la verdad del experimento no sería el del índice construido con los del experimento.
    El IVF por SQL entrena además con todos los vectores, como el del experimento."""
    for dataset in (IMAGES, AUDIO):
        for metric in (VectorMetric.COSINE, VectorMetric.EUCLIDEAN):
            answered = _measured(report, metric_operation(dataset, metric, QUERY))
            created = _measured(report, metric_operation(dataset, metric, CREATE_INDEX))
            built = _measured(report, metric_operation(dataset, metric, BUILD))
            assert {SQL_HNSW, SQL_IVF} <= created.keys()
            assert created[SQL_IVF]["lists"] == built[ENGINE_IVF]["listas"]
            assert created[SQL_HNSW]["zero_vectors"] + created[SQL_HNSW]["vectors"] == SIZE
            assert answered[SQL_HNSW]["recall_en_k"] == answered[ENGINE_HNSW]["recall_en_k"]
            assert answered[SQL_IVF]["recall_en_k"] == answered[ENGINE_IVF]["recall_en_k"]


def test_the_curve_sweeps_the_effort_and_marks_the_default(report):
    curve = [
        item
        for item in report.measurements
        if item.technique == ENGINE_HNSW
        and item.operation == metric_operation(IMAGES, VectorMetric.COSINE, CURVE)
    ]
    assert [item.extra["esfuerzo"] for item in curve] == [4, 8, EngineConfig().hnsw_ef_search]
    assert [item.extra["por_omision"] for item in curve] == [False, False, True]


def test_the_stages_memory_and_gallery_are_recorded(report):
    stages = _measured(report, dataset_operation(IMAGES, FEATURES))
    assert stages[EXTRACTION_STAGE]["objetos"] == SIZE
    assert all(stage["ms"] > 0 for stage in stages.values())
    memory = _measured(report, metric_operation(IMAGES, VectorMetric.COSINE, MEMORY))
    assert memory[FAISS_HNSW]["memoria_kib"] > 0
    assert memory[ENGINE_HNSW]["disco_kib"] > 0
    assert memory[ENGINE_HNSW]["memoria_python_kib"] > 0
    (gallery,) = _measured(report, dataset_operation(IMAGES, GALLERY)).values()
    assert len(gallery["exitos"]) == len(gallery["errores"]) == 1
    assert len(gallery["exitos"][0]["vecinos"]) == 2
    assert (
        gallery["exitos"][0]["precision_categoria"] >= gallery["errores"][0]["precision_categoria"]
    )


def test_saved_features_are_reused_and_refused_with_other_parameters(
    datasets, report, tmp_path: Path
):
    spec = datasets[0]
    files = spec.base(SIZE)
    config = EngineConfig(vocabulary_size=VOCABULARY, feature_workers=1)
    built = collection_features(files, config, 1, tmp_path)
    built.store.close()
    reused = collection_features(files, config, 1, tmp_path)
    reused.store.close()

    assert reused.reused and not built.reused
    assert np.array_equal(reused.vectors, built.vectors)
    with pytest.raises(StaleFeaturesError, match="otros parámetros"):
        collection_features(files, replace(config, vocabulary_size=VOCABULARY * 2), 1, tmp_path)


def test_the_results_are_saved_and_drawn(report, tmp_path: Path):
    saved = json.loads(report.save(tmp_path / "resultados").read_text(encoding="utf-8"))
    assert saved["name"] == "multimedia"
    assert saved["parameters"]["threads"] == 1
    assert saved["parameters"]["maquina"]["nucleos"] == os.cpu_count()
    assert "faiss" in saved["parameters"]["versiones"]
    assert "por_consulta_ms" not in report.render()
    pytest.importorskip("matplotlib")
    from benchmarks.plots import multimedia
    from benchmarks.plots.drawing import draw

    for figure in multimedia.FIGURES:
        assert draw(figure, saved, tmp_path / "figuras").stat().st_size > 0
