"""HNSW e IVF sobre una columna `VECTOR(n)` de punta a punta, por SQL.

Los vectores son de una mezcla de gaussianas con semilla fija: cada fila sale de uno de
unos pocos centros, como los embeddings de documentos de unos pocos temas. Los resultados
se comparan contra la búsqueda por fuerza bruta escrita aquí con numpy, sin pasar por el
gestor ni por FAISS.
"""

from pathlib import Path

import numpy as np
import pytest

from config import EngineConfig
from index.similarity import SimilarityEqualityError
from query.catalog import CatalogError
from query.engine import Engine
from query.index_methods import IndexOptionError
from query.similarity_search import SimilaritySearchError
from storage.types import InvalidValueError
from txn import LockManager, Session, TransactionManager

DIMENSION = 8
ROWS = 300
CENTERS = 6
SPREAD = 0.3
SEED = 7
K = 10
GROUPS = 3
CREATE_TABLE = f"CREATE TABLE docs (id INT PRIMARY KEY, grupo INT, emb VECTOR({DIMENSION}))"
# Con todo el esfuerzo, HNSW e IVF recorren todo y su respuesta es la exacta.
FULL_EFFORT = {"HNSW": f"ef_search = {ROWS}", "IVF": f"probes = {ROWS}"}
METHODS = ("HNSW", "IVF")
METRICS = ("euclidean", "dot", "cosine")
# Con el producto punto, un vector de norma chica no es el más parecido ni a sí mismo y el
# grafo de HNSW puede no llevar a él: aun con `ef_search` igual a las filas, en esta
# colección se le escapa uno de los 10 (medido). IVF con todas sus listas es exacto.
UNREACHABLE_WITH_DOT = ("HNSW", "dot")


def mixture(count: int, seed: int) -> np.ndarray:
    generator = np.random.default_rng(seed)
    centers = np.random.default_rng(SEED).standard_normal((CENTERS, DIMENSION))
    chosen = centers[generator.integers(0, CENTERS, count)]
    return (chosen + SPREAD * generator.standard_normal((count, DIMENSION))).astype(np.float32)


VECTORS = mixture(ROWS, SEED)
QUERIES = mixture(5, SEED + 1)


def brute_force(query: np.ndarray, metric: str, ids: list[int] | None = None) -> dict[int, float]:
    """Puntaje de cada fila (id = posición + 1) con la definición de cada métrica."""
    vectors = VECTORS.astype(np.float64)
    q = query.astype(np.float64)
    if metric == "euclidean":
        scores = 1.0 / (1.0 + np.linalg.norm(vectors - q, axis=1))
    elif metric == "dot":
        scores = vectors @ q
    else:
        scores = vectors @ q / (np.linalg.norm(vectors, axis=1) * np.linalg.norm(q))
    allowed = range(1, ROWS + 1) if ids is None else ids
    return {key: float(scores[key - 1]) for key in allowed}


def top(scores: dict[int, float], k: int) -> list[int]:
    return sorted(scores, key=lambda key: (-scores[key], key))[:k]


def vector_sql(vector: np.ndarray) -> str:
    return "[" + ", ".join(repr(float(component)) for component in vector) + "]"


def load(engine: Engine) -> None:
    engine.execute(CREATE_TABLE)
    rows = ", ".join(
        f"({key}, {key % GROUPS}, {vector_sql(vector)})"
        for key, vector in enumerate(VECTORS, start=1)
    )
    engine.execute(f"INSERT INTO docs VALUES {rows}")


def search(engine: Engine, query: np.ndarray, k: int = K, tail: str = "") -> list[tuple[int, float]]:
    found = engine.execute(
        f"SELECT id, SIMILARITY_SCORE() FROM docs WHERE SIMILAR_TO(emb, $1, k = {k}) {tail}",
        parameters=[query.tolist()],
    )
    return [(row[0], row[1]) for row in found.rows]


def ids_of(found: list[tuple[int, float]]) -> list[int]:
    return [key for key, _ in found]


def structure(engine: Engine, index: str = "i") -> dict:
    for described in engine.describe_table("docs")["indexes"]:
        if described["name"] == index:
            return described["structure"]
    raise KeyError(index)


@pytest.fixture
def config(tmp_path: Path) -> EngineConfig:
    return EngineConfig(data_directory=tmp_path / "datos")


@pytest.fixture
def engine(config: EngineConfig):
    with Engine(config) as opened:
        yield opened


@pytest.fixture(params=METHODS)
def indexed(request, engine: Engine) -> tuple[Engine, str]:
    load(engine)
    engine.execute(f"CREATE INDEX i ON docs USING {request.param} (emb) WITH (metric = 'cosine')")
    return engine, request.param


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("metric", METRICS)
def test_each_metric_matches_numpy_brute_force(engine, method, metric):
    load(engine)
    engine.execute(f"CREATE INDEX i ON docs USING {method} (emb) WITH (metric = {metric})")

    for query in QUERIES:
        found = search(engine, query, tail=f"USING {method} WITH {FULL_EFFORT[method]}")
        expected = brute_force(query, metric)

        assert [score for _, score in found] == pytest.approx(
            [expected[key] for key in ids_of(found)], rel=1e-5
        )
        if (method, metric) == UNREACHABLE_WITH_DOT:
            assert len(set(ids_of(found)) & set(top(expected, K))) >= K - 1
        else:
            assert ids_of(found) == top(expected, K)


@pytest.mark.parametrize("method", METHODS)
def test_the_default_effort_finds_most_true_neighbours(engine, method):
    load(engine)
    engine.execute(f"CREATE INDEX i ON docs USING {method} (emb)")

    recalls = [
        len(set(ids_of(search(engine, query))) & set(top(brute_force(query, "cosine"), K))) / K
        for query in QUERIES
    ]

    assert np.mean(recalls) >= 0.9


@pytest.mark.parametrize("method", METHODS)
def test_an_index_on_an_empty_table_fills_as_rows_arrive(engine, method):
    engine.execute(CREATE_TABLE)
    engine.execute(f"CREATE INDEX i ON docs USING {method} (emb)")

    assert search(engine, QUERIES[0]) == []

    engine.execute(f"INSERT INTO docs VALUES (1, 0, {vector_sql(VECTORS[0])})")

    assert search(engine, QUERIES[0]) == [(1, pytest.approx(brute_force(QUERIES[0], "cosine")[1]))]
    assert structure(engine)["vectors"] == 1


def test_a_single_vector_is_its_own_nearest(engine):
    engine.execute(CREATE_TABLE)
    engine.execute(f"INSERT INTO docs VALUES (1, 0, {vector_sql(VECTORS[0])})")
    engine.execute("CREATE INDEX i ON docs USING HNSW (emb)")

    assert search(engine, VECTORS[0], k=3) == [(1, pytest.approx(1.0))]


def test_a_vector_of_another_dimension_is_rejected_on_insert(indexed):
    engine, _ = indexed

    with pytest.raises(InvalidValueError, match=f"dimensión {DIMENSION} y llegó uno de dimensión 3"):
        engine.execute("INSERT INTO docs VALUES (1000, 0, [1, 2, 3])")
    assert structure(engine)["vectors"] == ROWS


def test_a_query_of_another_dimension_is_rejected_before_searching(indexed):
    engine, _ = indexed

    with pytest.raises(SimilaritySearchError, match=f"dimensión 3 y la columna 'emb' es VECTOR\\({DIMENSION}\\)"):
        engine.execute("SELECT id FROM docs WHERE SIMILAR_TO(emb, [1, 2, 3], k = 2)")


def test_a_file_query_on_a_vector_column_is_rejected(indexed):
    engine, _ = indexed

    with pytest.raises(SimilaritySearchError, match="se consulta con un vector"):
        engine.execute("SELECT id FROM docs WHERE SIMILAR_TO(emb, 'foto.jpg', k = 2)")


@pytest.mark.parametrize(("metric", "expected"), [("cosine", 0.0), ("dot", 0.0), ("euclidean", None)])
def test_a_zero_vector_stays_out_of_the_index_and_keeps_its_score(engine, metric, expected):
    load(engine)
    zero = ", ".join(["0"] * DIMENSION)
    engine.execute(f"INSERT INTO docs VALUES (1000, 0, [{zero}])")
    engine.execute(f"CREATE INDEX i ON docs USING HNSW (emb) WITH (metric = {metric})")

    found = dict(search(engine, QUERIES[0], k=ROWS + 1, tail=f"WITH ef_search = {ROWS}"))

    described = structure(engine)
    assert (described["vectors"], described["zero_vectors"]) == (ROWS, 1)
    zero_score = 1.0 / (1.0 + float(np.linalg.norm(QUERIES[0]))) if expected is None else expected
    assert found[1000] == pytest.approx(zero_score)


def test_null_vectors_are_left_out(indexed):
    engine, _ = indexed
    engine.execute("INSERT INTO docs VALUES (1000, 0, NULL)")

    assert structure(engine)["vectors"] == ROWS
    assert 1000 not in ids_of(search(engine, QUERIES[0], k=ROWS + 5))


def test_a_filter_that_leaves_fewer_than_k_rows_returns_them_all(indexed):
    engine, _ = indexed

    found = search(engine, QUERIES[0], tail="AND id <= 4")

    assert sorted(ids_of(found)) == [1, 2, 3, 4]
    assert ids_of(found) == top(brute_force(QUERIES[0], "cosine", [1, 2, 3, 4]), K)


def test_a_filter_is_applied_inside_the_approximate_search(config):
    """Con el umbral de búsqueda exacta en cero, el filtro va dentro de HNSW e IVF."""
    with Engine(EngineConfig(data_directory=config.data_directory, vector_exact_filter_rows=0)) as engine:
        load(engine)
        engine.execute("CREATE INDEX i ON docs USING HNSW (emb)")
        allowed = [key for key in range(1, ROWS + 1) if key % GROUPS == 1]

        found = search(engine, QUERIES[1], tail=f"AND grupo = 1 WITH ef_search = {ROWS}")

        assert ids_of(found) == top(brute_force(QUERIES[1], "cosine", allowed), K)


def test_another_metric_is_an_exact_search_and_the_plan_says_why(indexed):
    engine, _ = indexed

    result = engine.execute(
        "EXPLAIN ANALYZE SELECT id FROM docs WHERE SIMILAR_TO(emb, $1, k = 5) WITH METRIC = euclidean",
        parameters=[QUERIES[2].tolist()],
    )
    found = search(engine, QUERIES[2], k=5, tail="WITH METRIC = euclidean")

    assert ids_of(found) == top(brute_force(QUERIES[2], "euclidean"), 5)
    plan = str(result.plan)
    assert "VectorSearchScan" in plan
    assert "búsqueda exacta: el índice ordena con cosine" in plan
    assert "consulta extraída" not in plan


def test_the_plan_abbreviates_a_long_query_vector(indexed):
    engine, _ = indexed

    plan = str(engine.execute(
        "EXPLAIN SELECT id FROM docs WHERE SIMILAR_TO(emb, $1, k = 5)", parameters=[QUERIES[0].tolist()]
    ).plan)

    assert f"({DIMENSION} dimensiones)" in plan


def test_a_vector_search_is_not_shown_as_a_media_gallery(indexed):
    engine, _ = indexed

    assert engine.execute(
        "SELECT id FROM docs WHERE SIMILAR_TO(emb, $1, k = 2)", parameters=[QUERIES[0].tolist()]
    ).similarity is None


def test_an_update_moves_the_row_to_its_new_vector(indexed):
    engine, _ = indexed
    engine.execute(f"UPDATE docs SET emb = {vector_sql(QUERIES[3])} WHERE id = 42")

    assert search(engine, QUERIES[3], k=1) == [(42, pytest.approx(1.0))]
    assert structure(engine)["vectors"] == ROWS


def test_a_deleted_row_is_never_returned(indexed):
    engine, _ = indexed
    nearest = ids_of(search(engine, QUERIES[0], k=3))
    engine.execute(f"DELETE FROM docs WHERE id = {nearest[0]}")

    after = ids_of(search(engine, QUERIES[0], k=3))

    assert nearest[0] not in after
    assert after[:2] == nearest[1:]
    assert structure(engine)["vectors"] == ROWS - 1


def test_a_rollback_leaves_the_same_neighbours(indexed, config):
    engine, _ = indexed
    before = [search(engine, query) for query in QUERIES]
    with Session(engine, LockManager(config), TransactionManager()) as session:
        session.execute("BEGIN")
        session.execute(f"INSERT INTO docs VALUES (1000, 0, {vector_sql(QUERIES[0])})")
        session.execute("DELETE FROM docs WHERE grupo = 2")
        session.execute(f"UPDATE docs SET emb = {vector_sql(QUERIES[1])} WHERE id = 7")
        session.execute("ROLLBACK")

    assert [ids_of(search(engine, query)) for query in QUERIES] == [ids_of(found) for found in before]
    assert structure(engine)["vectors"] == ROWS


def test_deleting_a_third_of_the_rows_rebuilds_the_index_on_commit(indexed):
    engine, method = indexed
    engine.execute(f"DELETE FROM docs WHERE id <= {ROWS // 3 + 1}")
    engine.table("docs").flush()

    described = structure(engine)

    assert described["vectors"] == ROWS - ROWS // 3 - 1
    if method == "HNSW":
        assert (described["nodes"], described["tombstones"]) == (described["vectors"], 0)
    else:
        assert described["trained_on"] == described["vectors"]
    remaining = list(range(ROWS // 3 + 2, ROWS + 1))
    found = search(engine, QUERIES[0], tail=f"WITH {FULL_EFFORT[method]}")
    assert ids_of(found) == top(brute_force(QUERIES[0], "cosine", remaining), K)


def test_the_index_is_reopened_from_its_file(config, indexed):
    engine, _ = indexed
    before = search(engine, QUERIES[0])
    shape = structure(engine)
    engine.close()

    with Engine(config) as reopened:
        assert search(reopened, QUERIES[0]) == before
        assert structure(reopened) == shape


def test_a_commit_drops_the_stale_index_file(config, indexed):
    engine, _ = indexed
    engine.execute(f"INSERT INTO docs VALUES (1000, 0, {vector_sql(QUERIES[4])})")

    engine.table("docs").flush()

    assert not (config.data_directory / "docs.i.idx").exists()


def test_a_lost_index_file_is_rebuilt_from_the_table(config, indexed):
    engine, _ = indexed
    engine.execute(f"INSERT INTO docs VALUES (1000, 0, {vector_sql(QUERIES[4])})")
    expected = search(engine, QUERIES[4])
    engine.close()
    (config.data_directory / "docs.i.idx").unlink()

    with Engine(config) as reopened:
        assert search(reopened, QUERIES[4]) == expected
        assert structure(reopened)["vectors"] == ROWS + 1


def test_drop_index_removes_its_files(config, indexed):
    engine, _ = indexed

    engine.execute("DROP INDEX i")

    assert not list(config.data_directory.glob("docs.i.*"))


def test_an_equality_is_not_answered_by_the_index(indexed):
    engine, _ = indexed
    index = engine.table("docs").similarity_index("i")

    with pytest.raises(SimilarityEqualityError):
        index.search(tuple(map(float, VECTORS[0])))


def test_a_vocabulary_option_is_rejected_on_a_vector_column(engine):
    engine.execute(CREATE_TABLE)

    with pytest.raises(IndexOptionError, match="ya guarda sus vectores"):
        engine.execute("CREATE INDEX i ON docs USING HNSW (emb) WITH (vocabulary = 8)")


def test_an_integer_column_cannot_have_a_similarity_index(engine):
    engine.execute(CREATE_TABLE)

    with pytest.raises(CatalogError, match="VECTOR\\(n\\)"):
        engine.execute("CREATE INDEX i ON docs USING IVF (grupo)")


def test_a_filter_on_several_columns_reads_each_of_them(indexed):
    engine, _ = indexed
    allowed = [key for key in range(1, ROWS + 1) if key % GROUPS == 2 and key > 150]

    found = search(engine, QUERIES[2], tail="AND grupo = 2 AND id > 150")

    assert ids_of(found) == top(brute_force(QUERIES[2], "cosine", allowed), K)


def test_a_row_can_be_read_with_only_some_columns(indexed):
    engine, _ = indexed
    table = engine.table("docs")
    _, record = next(table.addressed_scan())

    partial = table.unpack_columns(record, [1])

    assert partial == (None, table.unpack(record)[1], None)
