"""El índice invertido como índice secundario: por la interfaz de `ColumnIndex` y, con el
método registrado como lo hará el SQL, a través de las transacciones del motor."""

from pathlib import Path

import pytest
from inverted_support import record_id_of

from config import EngineConfig
from index.inverted import (
    IndexLanguageMismatchError,
    InvertedIndex,
    InvertedIndexError,
    NotTextValueError,
    Ranker,
    RankingMethod,
    TextSearchEqualityError,
    TextSearchIndex,
)
from query import index_methods
from query.engine import Engine
from query.index_methods import IndexMethod
from sql.nodes import IndexType
from storage.record import RecordSerializer
from storage.record_id import RecordId
from storage.schema import Field, Schema
from storage.types import FieldType
from txn import LockManager, Session, TransactionManager

TEXT_LENGTH = 60
PRODUCTS = [
    (1, "Teclado mecánico inalámbrico"),
    (2, "Cable USB tipo C"),
    (3, None),
    (4, "Funda de cuero para teclado"),
    (5, "Cargador rápido USB"),
]
QUERIES = ["teclado", "usb cable", "funda cuero", "cargador teclado usb"]


@pytest.fixture
def serializer() -> RecordSerializer:
    return RecordSerializer(
        Schema(
            [
                Field("id", FieldType.INT, nullable=False),
                Field("texto", FieldType.STRING, TEXT_LENGTH),
            ]
        )
    )


@pytest.fixture
def path(config: EngineConfig) -> Path:
    return config.data_directory / "productos.texto.idx"


def rows_of(serializer: RecordSerializer) -> list[tuple[bytes, RecordId]]:
    return [(serializer.pack(row), record_id_of(row[0])) for row in PRODUCTS]


def found(index: TextSearchIndex, config: EngineConfig, query: str) -> list[RecordId]:
    ranked = Ranker(index.inverted_index, config).ranked(query, RankingMethod.BM25)
    return [document.record_id for document in ranked]


def test_build_indexes_every_row_with_text(
    path: Path, serializer: RecordSerializer, config: EngineConfig
):
    with TextSearchIndex(path, serializer, "texto", config) as index:
        index.build(rows_of(serializer))
        assert index.inverted_index.document_count == 4
        assert set(found(index, config, "teclado")) == {record_id_of(1), record_id_of(4)}
        assert index.describe()["documents"] == 4


def test_insert_delete_and_update_through_the_column_interface(
    path: Path, serializer: RecordSerializer, config: EngineConfig
):
    with TextSearchIndex(path, serializer, "texto", config) as index:
        index.build(rows_of(serializer))
        index.insert(serializer.pack((6, "Teclado numérico")), record_id_of(6))
        index.insert(serializer.pack((7, None)), record_id_of(7))
        assert record_id_of(6) in found(index, config, "numérico")
        assert index.delete(serializer.pack((2, "Cable USB tipo C")), record_id_of(2))
        assert not index.delete(serializer.pack((3, None)), record_id_of(3))
        assert found(index, config, "cable") == []
        index.update(
            serializer.pack((5, "Cargador rápido USB")),
            serializer.pack((5, "Cargador solar")),
            record_id_of(5),
        )
        assert found(index, config, "solar") == [record_id_of(5)]
        assert found(index, config, "usb") == []
        assert index.inverted_index.document_count == 4


def test_an_update_that_keeps_the_text_does_not_reindex(
    path: Path, serializer: RecordSerializer, config: EngineConfig
):
    with TextSearchIndex(path, serializer, "texto", config) as index:
        index.build(rows_of(serializer))
        row = serializer.pack((1, "Teclado mecánico inalámbrico"))
        index.update(row, row, record_id_of(1))
        assert index.inverted_index.deleted_count == 0


def test_equality_is_not_something_it_answers(
    path: Path, serializer: RecordSerializer, config: EngineConfig
):
    with TextSearchIndex(path, serializer, "texto", config) as index:
        with pytest.raises(TextSearchEqualityError, match="MATCH"):
            index.search("teclado")
        assert index.search(None) == []


def test_a_value_that_is_not_text_is_rejected(
    path: Path, serializer: RecordSerializer, config: EngineConfig
):
    with (
        TextSearchIndex(path, serializer, "id", config) as index,
        pytest.raises(NotTextValueError, match="int"),
    ):
        index.insert(serializer.pack((1, "teclado")), record_id_of(1))


def test_build_only_fills_an_empty_index(
    path: Path, serializer: RecordSerializer, config: EngineConfig
):
    with TextSearchIndex(path, serializer, "texto", config) as index:
        index.insert(serializer.pack((1, "teclado")), record_id_of(1))
        with pytest.raises(InvertedIndexError, match="vacío"):
            index.build(rows_of(serializer))


def test_reopening_keeps_what_was_written(
    path: Path, serializer: RecordSerializer, config: EngineConfig
):
    with TextSearchIndex(path, serializer, "texto", config) as index:
        index.build(rows_of(serializer))
        index.insert(serializer.pack((6, "Teclado numérico")), record_id_of(6))
    with TextSearchIndex(path, serializer, "texto", config) as reopened:
        assert set(found(reopened, config, "teclado")) == {
            record_id_of(1),
            record_id_of(4),
            record_id_of(6),
        }


def test_the_language_is_chosen_at_creation_and_checked_on_reopening(
    path: Path, serializer: RecordSerializer, config: EngineConfig
):
    with TextSearchIndex(path, serializer, "texto", config, language="en") as index:
        index.insert(serializer.pack((1, "running shoes")), record_id_of(1))
        assert index.inverted_index.language == "en"
        assert found(index, config, "run") == [record_id_of(1)]
    with pytest.raises(IndexLanguageMismatchError, match="'en'"):
        TextSearchIndex(path, serializer, "texto", config)


def test_files_of_lists_what_drop_index_has_to_remove(
    path: Path, serializer: RecordSerializer, config: EngineConfig
):
    with TextSearchIndex(path, serializer, "texto", config) as index:
        index.build(rows_of(serializer))
        index.insert(serializer.pack((6, "Teclado numérico")), record_id_of(6))
        index.flush()
    files = TextSearchIndex.files_of(path)
    assert files == InvertedIndex.files_of(path)
    assert sorted(path.parent.iterdir()) == sorted(files)


@pytest.fixture
def text_indexes(monkeypatch: pytest.MonkeyPatch) -> list[TextSearchIndex]:
    """Registra el método INVERTED como lo hará la integración con SQL, y anota cada índice
    que la tabla abre: el último es el que está en uso."""
    opened: list[TextSearchIndex] = []

    def opener(
        path: Path, serializer: RecordSerializer, column: str, config: EngineConfig
    ) -> TextSearchIndex:
        index = TextSearchIndex(path, serializer, column, config)
        opened.append(index)
        return index

    method = IndexMethod(
        method=IndexType.INVERTED,
        capabilities=frozenset(),
        opener=opener,
        refusal_for=lambda field: None,
        files_of=TextSearchIndex.files_of,
    )
    monkeypatch.setitem(index_methods._METHODS, IndexType.INVERTED, method)
    return opened


@pytest.fixture
def engine(config: EngineConfig, text_indexes: list[TextSearchIndex]):
    with Engine(config) as instance:
        yield instance


@pytest.fixture
def session(engine: Engine, config: EngineConfig):
    with Session(engine, LockManager(config), TransactionManager()) as instance:
        instance.execute(
            f"CREATE TABLE productos (id INT PRIMARY KEY, texto VARCHAR({TEXT_LENGTH}), precio INT)"
        )
        values = ", ".join(
            f"({number}, 'producto {number} {word}', {number})"
            for number, word in enumerate(
                ["teclado usb", "cable usb", "funda cuero", "teclado", "cargador usb"] * 6
            )
        )
        instance.execute(f"INSERT INTO productos VALUES {values}")
        instance.execute("CREATE INDEX ON productos USING INVERTED (texto)")
        yield instance


def searches(
    engine: Engine, config: EngineConfig, text_indexes: list[TextSearchIndex]
) -> dict[tuple[str, RankingMethod], list[tuple[float, int]]]:
    """Cada búsqueda como `(puntaje, id)` de mayor a menor, con el id de la fila en vez de
    su dirección: deshacer un borrado puede dejar la fila en otra ranura del heap."""
    table = engine.table("productos")
    ids = {address: table.unpack(record)[0] for address, record in table.addressed_scan()}
    ranker = Ranker(text_indexes[-1].inverted_index, config)
    return {
        (query, method): sorted(
            (-document.score, ids[document.record_id]) for document in ranker.ranked(query, method)
        )
        for query in QUERIES
        for method in RankingMethod
    }


def matching_ids(results: dict[tuple[str, RankingMethod], list[tuple[float, int]]]):
    return {key: sorted(identifier for _, identifier in found) for key, found in results.items()}


def test_a_commit_writes_the_pending_block(
    session: Session, text_indexes: list[TextSearchIndex]
):
    session.execute("INSERT INTO productos VALUES (100, 'teclado nuevo', 1)")
    index = text_indexes[-1].inverted_index
    assert index.pending_document_count == 0
    assert index.document_count == 31


def test_an_update_of_another_column_leaves_the_text_index_alone(
    session: Session, text_indexes: list[TextSearchIndex]
):
    session.execute("UPDATE productos SET precio = precio * 2")
    index = text_indexes[-1].inverted_index
    assert index.deleted_count == 0
    assert index.document_count == 30


@pytest.mark.parametrize(
    "changes",
    [
        ["INSERT INTO productos VALUES (100, 'teclado inalámbrico usb', 1)"],
        ["DELETE FROM productos WHERE id IN (0, 3, 7)"],
        ["UPDATE productos SET texto = 'funda de silicona' WHERE id < 5"],
        [
            "INSERT INTO productos VALUES (100, 'teclado inalámbrico usb', 1)",
            "DELETE FROM productos WHERE id IN (0, 3, 100)",
            "UPDATE productos SET texto = 'funda usb' WHERE id BETWEEN 10 AND 12",
            "UPDATE productos SET precio = 0 WHERE id > 20",
            "INSERT INTO productos VALUES (101, 'cable cuero', 1)",
        ],
    ],
)
def test_a_rollback_leaves_the_same_results(
    changes: list[str],
    engine: Engine,
    session: Session,
    config: EngineConfig,
    text_indexes: list[TextSearchIndex],
):
    """Antes de fusionar coinciden los documentos encontrados: los que dejó el deshacer como
    lápidas siguen en el df. Tras la fusión, también cada puntaje."""
    before = searches(engine, config, text_indexes)
    session.execute("BEGIN")
    for change in changes:
        session.execute(change)
    assert searches(engine, config, text_indexes) != before
    session.execute("ROLLBACK")
    assert matching_ids(searches(engine, config, text_indexes)) == matching_ids(before)
    text_indexes[-1].inverted_index.merge()
    assert searches(engine, config, text_indexes) == before
