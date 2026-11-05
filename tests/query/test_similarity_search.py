"""`SIMILAR_TO` de punta a punta: índice, consulta, plan, transacciones y errores.

La galería se genera en el test: cuatro familias de ruido desenfocado a escalas muy
distintas, y cada foto de una familia es otro recorte del mismo lienzo. SIFT no cambia con un desplazamiento, así que los vecinos de una foto son los de su
familia.
"""

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from tests.multimedia.media_support import (
    SAMPLE_RATE,
    TEXTURE_SIDE,
    TONE_AMPLITUDE,
    chirp,
    tone,
    write_audio,
    write_image,
)

from config import EngineConfig
from index.similarity import MixedMediaError
from index.vector import UnknownVectorMetricError
from multimedia import NotEnoughDescriptorsError
from query.catalog import CatalogError
from query.engine import Engine
from query.expressions import ExpressionError
from query.files import ForbiddenFileError
from query.index_methods import IndexOptionError
from query.loader import LoaderError
from query.similarity_search import SimilaritySearchError
from txn import LockManager, Session, TransactionManager

FAMILIES = 4
PHOTOS_PER_FAMILY = 6
CANVAS_SIDE = 2 * TEXTURE_SIDE
CROP_STEP = (11, 7)
FAMILY_BLURS = (1.5, 3.0, 6.0, 10.0)
NOISE_SEED = 5
CLIP_SECONDS = 1.0
LOW_HZ = 300.0
HIGH_HZ = 3_000.0
NOISE_LEVEL = 0.1
VOCABULARY = 16
K = 5
# Con 16 palabras y el coseno, de las 5 más parecidas a cada foto el 84 % es de su familia
# (medido sobre esta galería); la familia más desenfocada da solo 14 descriptores por foto y
# es la que baja el promedio.
MIN_FAMILY_PURITY = 0.75
CREATE_TABLE = "CREATE TABLE fotos (id INT PRIMARY KEY, ruta VARCHAR(200), familia INT)"
CREATE_HNSW = (
    f"CREATE INDEX idx_hnsw ON fotos USING HNSW (ruta) "
    f"WITH (METRIC = cosine, vocabulary = {VOCABULARY})"
)
CREATE_IVF = (
    f"CREATE INDEX idx_ivf ON fotos USING IVF (ruta) "
    f"WITH (METRIC = cosine, vocabulary = {VOCABULARY})"
)


def canvas(family: int) -> np.ndarray:
    """El lienzo de una familia: manchas de un tamaño propio, que SIFT encuentra a otra
    escala. El ruido no se repite, así que dos recortes nunca dan el mismo histograma."""
    noise = np.random.default_rng(NOISE_SEED + family).integers(0, 256, (CANVAS_SIDE,) * 2)
    blurred = cv2.GaussianBlur(noise.astype(np.uint8), (0, 0), FAMILY_BLURS[family])
    return cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX)


def photo(directory: Path, family: int, number: int) -> Path:
    top, left = number * CROP_STEP[0], number * CROP_STEP[1]
    crop = canvas(family)[top : top + TEXTURE_SIDE, left : left + TEXTURE_SIDE]
    return write_image(directory / f"f{family}_{number}.png", np.ascontiguousarray(crop))


def sine(hertz: float) -> np.ndarray:
    times = np.arange(round(CLIP_SECONDS * SAMPLE_RATE)) / SAMPLE_RATE
    return (TONE_AMPLITUDE * np.sin(2 * np.pi * hertz * times)).astype(np.float32)


def settings(data: Path, *media: Path) -> EngineConfig:
    return EngineConfig(data_directory=data, import_directories=media, feature_workers=1)


@pytest.fixture(scope="module")
def shared(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[int, tuple[Path, int]]]:
    """La galería y una base con la tabla y su índice HNSW, construidas una vez: extraer
    cada foto es lo caro. Cada test trabaja sobre una copia de la base."""
    root = tmp_path_factory.mktemp("galeria")
    directory = root / "medios"
    directory.mkdir()
    rows = {}
    for family in range(FAMILIES):
        for number in range(PHOTOS_PER_FAMILY):
            rows[len(rows) + 1] = (photo(directory, family, number), family)
    built = Engine(settings(root / "base", directory))
    built.execute(CREATE_TABLE)
    values = ", ".join(f"({key}, '{path}', {family})" for key, (path, family) in rows.items())
    built.execute(f"INSERT INTO fotos VALUES {values}")
    built.execute(CREATE_HNSW)
    built.close()
    return root, rows


@pytest.fixture
def media(tmp_path: Path) -> Path:
    directory = tmp_path / "medios"
    directory.mkdir()
    return directory


@pytest.fixture
def gallery(shared) -> dict[int, tuple[Path, int]]:
    """`id → (ruta, familia)` de las fotos de la galería."""
    return shared[1]


@pytest.fixture
def config(tmp_path: Path, media: Path, shared) -> EngineConfig:
    return settings(tmp_path / "datos", media, shared[0] / "medios")


@pytest.fixture
def engine(config: EngineConfig):
    created = Engine(config)
    yield created
    created.close()


@pytest.fixture
def fotos(shared, config: EngineConfig):
    shutil.copytree(shared[0] / "base", config.data_directory)
    created = Engine(config)
    yield created
    created.close()


def ids(engine: Engine, sql: str) -> list[int]:
    return [row[0] for row in engine.execute(sql).rows]


def similar(query: Path, k: int = K, tail: str = "") -> str:
    return f"SELECT id FROM fotos WHERE SIMILAR_TO('{query}', k = {k}) {tail}"


def family_of(gallery, key: int) -> int:
    return gallery[key][1]


def test_the_query_of_the_statement_runs_as_written(fotos, gallery):
    query = gallery[1][0]

    result = fotos.execute(
        f"SELECT * FROM fotos WHERE SIMILAR_TO('{query}', k=10) USING HNSW WITH METRIC=cosine"
    )

    assert len(result.rows) == 10
    assert result.columns == ("id", "ruta", "familia")
    assert result.rows[0][0] == 1


def test_each_photo_finds_itself_and_mostly_its_family(fotos, gallery):
    purities = []
    for key, (path, family) in gallery.items():
        found = ids(fotos, similar(path))
        assert found[0] == key
        purities.append(sum(family_of(gallery, other) == family for other in found) / K)

    assert sum(purities) / len(purities) >= MIN_FAMILY_PURITY


def test_scores_come_from_highest_to_lowest_without_sorting(fotos, gallery):
    result = fotos.execute(
        f"SELECT id, SIMILARITY_SCORE() AS score FROM fotos "
        f"WHERE SIMILAR_TO('{gallery[1][0]}', k = {K}) ORDER BY score DESC"
    )

    scores = [row[1] for row in result.rows]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(1.0)
    assert "Sort" not in str(result.plan)


def test_an_unnamed_score_is_called_similarity_score(fotos, gallery):
    result = fotos.execute(
        f"SELECT id, SIMILARITY_SCORE() FROM fotos WHERE SIMILAR_TO('{gallery[1][0]}', k = 2)"
    )

    assert result.columns == ("id", "similarity_score")


def test_the_column_can_be_named(fotos, gallery):
    found = ids(fotos, f"SELECT id FROM fotos WHERE SIMILAR_TO(ruta, '{gallery[9][0]}', k = 3)")

    assert found[0] == 9


def test_hnsw_and_ivf_agree_with_the_exact_search(fotos, gallery):
    fotos.execute(CREATE_IVF)
    query = gallery[3][0]
    exact = ids(fotos, similar(query, tail="USING HNSW WITH METRIC = dot"))
    exact_cosine = ids(
        fotos, f"SELECT id FROM fotos WHERE SIMILAR_TO('{query}', k = {len(gallery)}) USING IVF"
    )

    hnsw = ids(fotos, similar(query, tail="USING HNSW WITH ef_search = 100"))
    ivf = ids(fotos, similar(query, tail=f"USING IVF WITH probes = {len(gallery)}"))

    assert exact
    assert hnsw == ivf == exact_cosine[:K]


def test_two_similarity_indexes_need_a_using(fotos, gallery):
    fotos.execute(CREATE_IVF)

    with pytest.raises(SimilaritySearchError, match="varios índices"):
        fotos.execute(similar(gallery[1][0]))
    assert ids(fotos, similar(gallery[1][0], tail="USING IVF"))[0] == 1


def test_the_rest_of_the_where_filters_before_searching(fotos, gallery):
    found = ids(fotos, similar(gallery[1][0], k=K, tail="AND familia = 2"))

    assert len(found) == K
    assert {family_of(gallery, key) for key in found} == {2}


def test_the_filter_appears_in_the_plan_before_the_search(fotos, gallery):
    plan = fotos.execute(
        f"EXPLAIN ANALYZE SELECT id FROM fotos WHERE SIMILAR_TO('{gallery[1][0]}', k = 3) "
        "AND id < 10"
    ).plan

    scan = plan.children[0].children[0]
    assert scan.operation == "VectorSearchScan"
    assert "filtro antes de buscar: id < 10" in scan.detail
    assert "9 filas cumplen el filtro" in scan.detail
    assert scan.children


def test_another_metric_compares_against_every_row(fotos, gallery):
    result = fotos.execute(
        f"EXPLAIN ANALYZE {similar(gallery[1][0], tail='WITH METRIC = euclidean')}"
    )

    assert "búsqueda exacta: el índice ordena con cosine y la consulta pide euclidean" in str(
        result.plan
    )
    assert ids(fotos, similar(gallery[1][0], tail="WITH METRIC = euclidean"))[0] == 1


def test_inserted_and_deleted_rows_are_kept_in_the_index(fotos, gallery, media):
    new = photo(media, 3, 9)
    fotos.execute(f"INSERT INTO fotos VALUES (100, '{new}', 3)")
    assert ids(fotos, similar(new, k=1)) == [100]

    fotos.execute("DELETE FROM fotos WHERE id = 100")
    assert 100 not in ids(fotos, similar(new))


def test_an_updated_path_is_searched_by_its_new_file(fotos, gallery):
    fotos.execute(f"UPDATE fotos SET ruta = '{gallery[20][0]}' WHERE id = 1")

    assert set(ids(fotos, similar(gallery[20][0], k=2))) == {1, 20}


def test_a_rollback_leaves_the_same_neighbors(fotos, gallery, config, media):
    queries = [gallery[key][0] for key in (1, 13)]
    before = [ids(fotos, similar(query)) for query in queries]
    with Session(fotos, LockManager(config), TransactionManager()) as session:
        session.execute("BEGIN")
        session.execute(f"INSERT INTO fotos VALUES (100, '{photo(media, 0, 9)}', 0)")
        session.execute("DELETE FROM fotos WHERE familia = 2")
        session.execute(f"UPDATE fotos SET ruta = '{gallery[1][0]}' WHERE id = 7")
        session.execute("ROLLBACK")

    assert [ids(fotos, similar(query)) for query in queries] == before


def test_the_index_survives_reopening_and_losing_its_vector_file(config, fotos, gallery):
    before = ids(fotos, similar(gallery[5][0]))
    fotos.close()

    reopened = Engine(config)
    assert ids(reopened, similar(gallery[5][0])) == before
    reopened.close()

    vector_file = next(Path(config.data_directory).glob("fotos.idx_hnsw.idx"))
    vector_file.unlink()
    rebuilt = Engine(config)
    assert ids(rebuilt, similar(gallery[5][0])) == before
    rebuilt.close()


def test_dropping_the_index_removes_every_file(fotos, config):
    fotos.execute("DROP INDEX idx_hnsw ON fotos")

    assert not list(Path(config.data_directory).glob("fotos.idx_hnsw*"))


def test_an_index_on_an_empty_table_cannot_train_its_vocabulary(engine, config):
    engine.execute(CREATE_TABLE)

    with pytest.raises(NotEnoughDescriptorsError):
        engine.execute(CREATE_HNSW)
    assert not list(Path(config.data_directory).glob("fotos.idx_hnsw*"))
    assert [index.name for index in engine.table("fotos").definition.indexes] == ["pk_fotos"]


@pytest.mark.parametrize(
    ("options", "error", "message"),
    [
        ("M = 0", IndexOptionError, "entero positivo"),
        ("METRIC = 3", IndexOptionError, "nombre de una métrica"),
        ("METRIC = manhattan", UnknownVectorMetricError, "cosine"),
        ("lists = 4", CatalogError, "lists"),
    ],
)
def test_bad_index_options_leave_nothing_behind(
    engine, gallery, config, options, error, message
):
    engine.execute(CREATE_TABLE)
    engine.execute(f"INSERT INTO fotos VALUES (1, '{gallery[1][0]}', 0)")

    with pytest.raises(error, match=message):
        engine.execute(f"CREATE INDEX i ON fotos USING HNSW (ruta) WITH ({options})")
    assert not list(Path(config.data_directory).glob("fotos.i*"))


def test_a_column_cannot_mix_images_and_audio(engine, gallery, media):
    sound = write_audio(media / "tono.wav", tone(1.0))
    engine.execute(CREATE_TABLE)
    engine.execute(f"INSERT INTO fotos VALUES (1, '{gallery[1][0]}', 0), (2, '{sound}', 0)")

    with pytest.raises(MixedMediaError, match="imágenes o solo audios"):
        engine.execute(CREATE_HNSW)


def test_an_audio_query_on_an_image_column_is_rejected(fotos, media):
    sound = write_audio(media / "tono.wav", tone(1.0))

    with pytest.raises(MixedMediaError, match="guarda imágenes"):
        fotos.execute(similar(sound))


def test_audio_columns_find_their_own_clips(engine, media):
    noise = np.random.default_rng(NOISE_SEED).standard_normal(len(sine(LOW_HZ)))
    clips = {
        1: write_audio(media / "grave.wav", sine(LOW_HZ)),
        2: write_audio(media / "agudo.wav", sine(HIGH_HZ)),
        3: write_audio(media / "barrido.wav", chirp(CLIP_SECONDS)),
        4: write_audio(media / "ruido.wav", (noise * NOISE_LEVEL).astype(np.float32)),
    }
    engine.execute("CREATE TABLE sonidos (id INT PRIMARY KEY, ruta VARCHAR(200))")
    values = ", ".join(f"({key}, '{path}')" for key, path in clips.items())
    engine.execute(f"INSERT INTO sonidos VALUES {values}")
    engine.execute(
        "CREATE INDEX i ON sonidos USING IVF (ruta) WITH (METRIC = euclidean, vocabulary = 8)"
    )

    for key, path in clips.items():
        found = engine.execute(
            f"SELECT id FROM sonidos WHERE SIMILAR_TO('{path}', k = 2) "
            "USING IVF WITH METRIC = euclidean"
        ).rows
        assert found[0][0] == key


@pytest.mark.parametrize(
    ("where", "message"),
    [
        ("SIMILAR_TO('{q}', k = 3) OR id = 1", "ni combinar con OR"),
        ("NOT SIMILAR_TO('{q}', k = 3)", "ni combinar con OR"),
        ("SIMILAR_TO('{q}', k = 3) AND SIMILAR_TO('{q}', k = 2)", "un solo SIMILAR_TO"),
        ("SIMILAR_TO('{q}')", "cuántas filas devolver"),
        ("SIMILAR_TO('{q}', k = 0)", "entero positivo"),
        ("SIMILAR_TO('{q}', k = 'diez')", "entero positivo"),
        ("SIMILAR_TO('{q}', k = 3, n = 2)", "cuántas filas devolver"),
        ("SIMILAR_TO(42, k = 3)", "ruta de un archivo"),
        ("SIMILAR_TO(1, '{q}', k = 3)", "columna de las rutas"),
        ("SIMILAR_TO('{q}', k = 3) AND MATCH(ruta, 'x')", "no las dos"),
    ],
)
def test_a_malformed_similar_to_is_rejected(fotos, gallery, where, message):
    with pytest.raises(SimilaritySearchError, match=message):
        fotos.execute(f"SELECT id FROM fotos WHERE {where.format(q=gallery[1][0])}")


@pytest.mark.parametrize(
    ("tail", "message"),
    [
        ("USING BM25", "USING HNSW o USING IVF"),
        ("USING IVF", "no hay un índice IVF"),
        ("WITH probes = 4", "admite metric y ef_search"),
        ("WITH ef_search = 0", "entero positivo"),
    ],
)
def test_using_and_with_must_fit_the_index(fotos, gallery, tail, message):
    with pytest.raises(SimilaritySearchError, match=message):
        fotos.execute(similar(gallery[1][0], tail=tail))


def test_an_unknown_metric_is_rejected(fotos, gallery):
    with pytest.raises(UnknownVectorMetricError):
        fotos.execute(similar(gallery[1][0], tail="WITH METRIC = manhattan"))


def test_a_score_without_similar_to_is_rejected(fotos):
    with pytest.raises(SimilaritySearchError, match="no tiene ninguno en su WHERE"):
        fotos.execute("SELECT SIMILARITY_SCORE() FROM fotos")


def test_a_score_takes_no_arguments(fotos, gallery):
    with pytest.raises(SimilaritySearchError, match="no recibe argumentos"):
        fotos.execute(
            f"SELECT SIMILARITY_SCORE(1) FROM fotos WHERE SIMILAR_TO('{gallery[1][0]}', k = 2)"
        )


def test_a_query_outside_the_allowed_directories_is_rejected(fotos, tmp_path):
    outside = photo(tmp_path, 0, 0)

    with pytest.raises(ForbiddenFileError):
        fotos.execute(similar(outside))


def test_a_missing_query_file_is_rejected(fotos, media):
    with pytest.raises(LoaderError):
        fotos.execute(similar(media / "no-existe.png"))


def test_similar_to_outside_a_select_where_says_where_it_goes(fotos, gallery):
    query = gallery[1][0]
    with pytest.raises(ExpressionError, match="va en el WHERE de un SELECT"):
        fotos.execute(f"SELECT SIMILAR_TO('{query}', k = 2) FROM fotos")
    with pytest.raises(ExpressionError, match="va en el WHERE de un SELECT"):
        fotos.execute(f"DELETE FROM fotos WHERE SIMILAR_TO('{query}', k = 2)")


def test_a_path_column_answers_no_equality(fotos, gallery):
    with pytest.raises(Exception, match="no responde igualdades"):
        fotos.table("fotos").similarity_index("idx_hnsw").search(str(gallery[1][0]))
