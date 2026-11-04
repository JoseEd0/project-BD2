"""El índice invertido en el catálogo: `CREATE INDEX … USING INVERTED`, sus opciones, las
columnas que admite y los archivos que deja o se lleva."""

from pathlib import Path

import pytest

from config import EngineConfig
from index.inverted import InvertedIndex
from query.catalog import CatalogError
from query.engine import Engine
from query.index_methods import IndexOptionError
from text import UnknownLanguageError

TEXTS = ["Teclado mecánico inalámbrico", "Cable USB tipo C", "Funda de cuero", None]


@pytest.fixture
def productos(engine: Engine) -> Engine:
    engine.execute(
        "CREATE TABLE productos (id INT PRIMARY KEY, nombre VARCHAR(40), texto TEXT, precio INT)"
    )
    for number, text in enumerate(TEXTS):
        value = "NULL" if text is None else f"'{text}'"
        engine.execute(f"INSERT INTO productos VALUES ({number}, 'p{number}', {value}, {number})")
    return engine


def index_files(config: EngineConfig, index: str) -> list[Path]:
    return sorted(Path(config.data_directory).glob(f"productos.{index}.idx*"))


def test_an_inverted_index_indexes_every_row_with_text(productos: Engine):
    message = productos.execute("CREATE INDEX ON productos USING INVERTED (texto)").message
    assert message == "índice 'idx_productos_texto' creado sobre productos.texto (INVERTED)"
    structure = productos.describe_table("productos")["indexes"][-1]["structure"]
    assert structure["kind"] == "inverted"
    assert structure["documents"] == len([text for text in TEXTS if text is not None])
    assert structure["language"] == "es"


def test_a_varchar_column_takes_one_as_well(productos: Engine):
    productos.execute("CREATE INDEX por_nombre ON productos USING INVERTED (nombre)")
    assert productos.table("productos").definition.index_on_name("por_nombre").column == "nombre"


@pytest.mark.parametrize("language", ["en", "EN", " es "])
def test_the_language_is_an_option(productos: Engine, language: str):
    options = f"WITH (idioma='{language}')"
    productos.execute(f"CREATE INDEX i ON productos USING INVERTED (texto) {options}")
    index = productos.table("productos").text_search_index("i").inverted_index
    assert index.language == language.strip().lower()


@pytest.mark.parametrize(
    ("options", "error", "message"),
    [
        ("WITH (idioma='klingon')", UnknownLanguageError, "idioma desconocido 'klingon'"),
        ("WITH (idioma=5)", IndexOptionError, "código de un idioma"),
        ("WITH (k1=1.2)", CatalogError, "no admite opciones: k1"),
    ],
)
def test_an_option_that_does_not_serve_leaves_nothing_behind(
    productos: Engine, config: EngineConfig, options: str, error: type, message: str
):
    with pytest.raises(error, match=message):
        productos.execute(f"CREATE INDEX i ON productos USING INVERTED (texto) {options}")
    assert productos.table_of_index("i") is None
    assert index_files(config, "i") == []


@pytest.mark.parametrize("column", ["id", "precio"])
def test_only_text_columns_take_one(productos: Engine, column: str):
    with pytest.raises(CatalogError, match="necesita una columna de texto"):
        productos.execute(f"CREATE INDEX ON productos USING INVERTED ({column})")


def test_a_table_kept_in_key_order_does_not_take_one(engine: Engine):
    engine.execute("CREATE TABLE notas (id INT PRIMARY KEY INDEX SEQ, texto TEXT)")
    with pytest.raises(CatalogError, match="heap file"):
        engine.execute("CREATE INDEX ON notas USING INVERTED (texto)")


def test_it_can_be_declared_with_the_column(engine: Engine):
    engine.execute("CREATE TABLE notas (id INT PRIMARY KEY, texto TEXT INDEX INVERTED)")
    engine.execute("INSERT INTO notas VALUES (1, 'apuntes de árboles B+')")
    index = engine.table("notas").text_search_index("idx_notas_texto").inverted_index
    assert index.document_count == 1


def test_a_unique_column_needs_an_index_that_finds_equal_values(engine: Engine):
    with pytest.raises(CatalogError, match="no busca por igualdad"):
        engine.execute("CREATE TABLE notas (id INT, texto VARCHAR(20) UNIQUE INDEX INVERTED)")
    assert not engine.table_names()


def test_a_file_loaded_with_an_inverted_index_is_a_heap_with_that_index(
    engine: Engine, config: EngineConfig
):
    """`USING INDEX` en un `FROM FILE` elige la organización solo si el método identifica
    filas; con un índice invertido la tabla es un heap y el índice se construye al cargar,
    igual que con un R-Tree."""
    path = config.data_directory / "fichas.csv"
    path.write_text("id,texto\n1,cable usb\n2,teclado usb\n3,funda\n", encoding="utf-8")
    engine.execute(f"CREATE TABLE fichas FROM FILE '{path}' USING INDEX INVERTED(texto)")
    definition = engine.table("fichas").definition
    assert definition.primary_key is None
    assert [(index.column, index.method.value) for index in definition.indexes] == [
        ("texto", "INVERTED")
    ]
    index = engine.table("fichas").text_search_index("idx_fichas_texto").inverted_index
    assert index.document_count == 3


def test_dropping_the_index_removes_every_file_of_it(productos: Engine, config: EngineConfig):
    productos.execute("CREATE INDEX i ON productos USING INVERTED (texto)")
    productos.execute("INSERT INTO productos VALUES (9, 'p9', 'teclado numérico', 9)")
    productos.table("productos").flush()
    path = config.data_directory / "productos.i.idx"
    assert index_files(config, "i") == sorted(InvertedIndex.files_of(path))
    productos.execute("DROP INDEX i")
    assert index_files(config, "i") == []


def test_dropping_the_table_removes_its_index(productos: Engine, config: EngineConfig):
    productos.execute("CREATE INDEX i ON productos USING INVERTED (texto)")
    productos.execute("DROP TABLE productos")
    assert not list(Path(config.data_directory).glob("productos.*"))


def test_reopening_keeps_the_index_and_its_language(config: EngineConfig):
    with Engine(config) as engine:
        engine.execute("CREATE TABLE notas (id INT PRIMARY KEY, texto TEXT)")
        engine.execute("INSERT INTO notas VALUES (1, 'the quick brown fox')")
        engine.execute("CREATE INDEX i ON notas USING INVERTED (texto) WITH (idioma='en')")
    with Engine(config) as engine:
        index = engine.table("notas").text_search_index("i").inverted_index
        assert (index.language, index.document_count) == ("en", 1)
