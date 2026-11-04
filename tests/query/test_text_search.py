"""Búsqueda de texto en SQL: `MATCH`, `SCORE()`, `USING TF_IDF | BM25` y `WITH (k1, b)`.

Cada consulta se compara con el oráculo del ranking, que aplica las fórmulas directo a los
textos de la tabla, sin índice: el plan puede cambiar —top-k, orden perezoso, filtros,
JOIN—, la respuesta no.
"""

import pytest
from tests.index.inverted.inverted_support import (
    DEFAULT_B,
    DEFAULT_K1,
    TOLERANCE,
    Oracle,
    expected_order,
    random_texts,
)

from config import EngineConfig
from index.inverted import RankingMethod, RankingParameterError
from query.engine import Engine, QueryResult
from query.expressions import AmbiguousColumnError, ExpressionError, UnknownColumnError
from query.operators import UnsupportedQueryError
from query.text_search import TextSearchError
from storage.record_id import RecordId
from text import Analyzer
from txn import LockManager, Session, TransactionManager

PRODUCTS = [
    (1, "Logi", 120, "Teclado mecánico inalámbrico con luz para juegos"),
    (2, "Anker", 15, "Cable USB tipo C de carga rápida, dos metros"),
    (3, "Anker", 35, "Cargador rápido USB de pared con dos puertos"),
    (4, "Lumo", 25, "Funda de cuero para teléfono, color marrón"),
    (5, "Logi", 40, "Mouse inalámbrico silencioso con receptor USB"),
    (6, "Lumo", 22, "Teclado numérico USB para laptop"),
    (7, "Sonar", 90, "Audífonos inalámbricos con cancelación de ruido"),
    (8, "Anker", 45, "Hub USB con cuatro puertos y un cable corto"),
    (9, "Lumo", 30, "Lámpara de escritorio con puerto USB"),
    (10, "Sonar", 10, None),
]
BRANDS = [("Logi", "Suiza"), ("Anker", "China"), ("Lumo", "Perú"), ("Sonar", "Japón")]
QUERIES = ["usb", "teclado inalámbrico", "cable usb rápido", "puertos de carga para laptop"]
TEXT_POSITION = 3
ARTICLES = [
    (1, "Introducción", "Machine learning para principiantes: modelos y datos"),
    (2, "Redes", "Deep learning con redes neuronales"),
    (3, "Bases", "Bases de datos relacionales y SQL"),
    (4, "Visión", "Visión por computadora con machine learning y deep learning"),
    (5, "Cocina", "Recetas de cocina peruana"),
]
DOCUMENTS = [
    (1, "Una base de datos vectorial guarda embeddings"),
    (2, "Bases de datos relacionales con SQL"),
    (3, "El índice vectorial HNSW"),
    (4, "Recetas de cocina"),
]
RANDOM_DOCUMENTS = 60
RANDOM_SEED = 10


def literal(value: object) -> str:
    if value is None:
        return "NULL"
    return f"'{value}'" if isinstance(value, str) else str(value)


def insert(engine: Engine, table: str, rows: list[tuple]) -> None:
    values = ", ".join(f"({', '.join(map(literal, row))})" for row in rows)
    engine.execute(f"INSERT INTO {table} VALUES {values}")


@pytest.fixture
def tienda(engine: Engine) -> Engine:
    engine.execute(
        "CREATE TABLE productos (id INT PRIMARY KEY, marca VARCHAR(10), precio INT, texto TEXT)"
    )
    insert(engine, "productos", PRODUCTS)
    engine.execute("CREATE INDEX idx_texto ON productos USING INVERTED (texto)")
    engine.execute("CREATE TABLE marcas (marca VARCHAR(10), pais VARCHAR(10))")
    insert(engine, "marcas", BRANDS)
    return engine


def oracle_ranking(
    engine: Engine,
    config: EngineConfig,
    query: str,
    method: RankingMethod,
    k1: float = DEFAULT_K1,
    b: float = DEFAULT_B,
    table_name: str = "productos",
) -> list[tuple[int, float]]:
    """`(id, puntaje)` de cada fila que el oráculo encuentra, en el orden que debe salir."""
    table = engine.table(table_name)
    texts: dict[RecordId, str] = {}
    ids: dict[RecordId, int] = {}
    for address, record in table.addressed_scan():
        row = table.unpack(record)
        assert address is not None
        if row[-1] is not None:
            texts[address] = row[-1]
            ids[address] = row[0]
    scores = Oracle.of(texts, Analyzer(config)).scores(query, method, k1, b)
    return [(ids[address], scores[address]) for address in expected_order(scores)]


def assert_same_ranking(rows: tuple, expected: list[tuple[int, float]]) -> None:
    """Mismos puntajes y mismo orden; dos filas solo pueden cambiar de lugar si sus
    puntajes difieren menos que el redondeo de sumar en otro orden."""
    assert len(rows) == len(expected)
    scores = dict(expected)
    for (identifier, score), (expected_id, expected_score) in zip(rows, expected, strict=True):
        assert score == pytest.approx(scores[identifier], abs=TOLERANCE)
        if identifier != expected_id:
            assert score == pytest.approx(expected_score, abs=TOLERANCE)


def operations(result: QueryResult) -> list[str]:
    assert result.plan is not None
    return [line.split(":")[0].strip("└├─│ ") for line in result.plan.render().splitlines()]


def scan_detail(result: QueryResult) -> str:
    assert result.plan is not None
    return next(line for line in result.plan.render().splitlines() if "TextSearchScan" in line)


def test_the_first_example_of_the_statement_works_as_written(engine: Engine, config: EngineConfig):
    engine.execute("CREATE TABLE documentos (id INT PRIMARY KEY, contenido TEXT)")
    insert(engine, "documentos", DOCUMENTS)
    engine.execute("CREATE INDEX ON documentos USING INVERTED (contenido)")
    result = engine.execute(
        "SELECT * FROM documentos WHERE MATCH(contenido, 'base de datos vectorial')\n"
        "    USING TF_IDF LIMIT 10;"
    )
    assert result.columns == ("id", "contenido")
    expected = oracle_ranking(
        engine, config, "base de datos vectorial", RankingMethod.TF_IDF, table_name="documentos"
    )
    assert [row[0] for row in result.rows] == [identifier for identifier, _ in expected]
    assert "top 10" in scan_detail(result)


def test_the_second_example_of_the_statement_works_as_written(
    engine: Engine, config: EngineConfig
):
    engine.execute("CREATE TABLE articulos (id INT PRIMARY KEY, titulo VARCHAR(20), texto TEXT)")
    insert(engine, "articulos", ARTICLES)
    engine.execute("CREATE INDEX ON articulos USING INVERTED (texto)")
    result = engine.execute(
        "SELECT *, SCORE() as relevancia FROM articulos\n"
        "    WHERE MATCH(texto, 'machine learning')\n"
        "    USING BM25\n"
        "    ORDER BY relevancia DESC;"
    )
    assert result.columns == ("id", "titulo", "texto", "relevancia")
    expected = oracle_ranking(
        engine, config, "machine learning", RankingMethod.BM25, table_name="articulos"
    )
    assert_same_ranking(tuple((row[0], row[-1]) for row in result.rows), expected)
    assert {row[0] for row in result.rows} == {1, 2, 4}
    assert "ExternalSort" not in operations(result)


@pytest.mark.parametrize("method", list(RankingMethod))
@pytest.mark.parametrize("query", QUERIES)
def test_every_match_answers_what_the_oracle_does(
    tienda: Engine, config: EngineConfig, method: RankingMethod, query: str
):
    rows = tienda.execute(
        f"SELECT id, SCORE() FROM productos WHERE MATCH(texto, '{query}') USING {method.value}"
    ).rows
    assert_same_ranking(rows, oracle_ranking(tienda, config, query, method))


@pytest.mark.parametrize(("k1", "b"), [(DEFAULT_K1, DEFAULT_B), (0.0, 0.5), (2.0, 0.0), (0.5, 1)])
def test_random_texts_with_any_bm25_parameters_answer_what_the_oracle_does(
    engine: Engine, config: EngineConfig, k1: float, b: float
):
    engine.execute("CREATE TABLE fichas (id INT PRIMARY KEY, texto TEXT)")
    insert(engine, "fichas", list(enumerate(random_texts(RANDOM_DOCUMENTS, RANDOM_SEED))))
    engine.execute("CREATE INDEX ON fichas USING INVERTED (texto)")
    for query in ["teclado cable", "usb 4k rojo", "ñandu para año azul"]:
        rows = engine.execute(
            f"SELECT id, SCORE() FROM fichas WHERE MATCH(texto, '{query}') "
            f"WITH (k1 = {k1}, b = {b})"
        ).rows
        expected = oracle_ranking(
            engine, config, query, RankingMethod.BM25, k1, b, table_name="fichas"
        )
        assert_same_ranking(rows, expected)


def test_bm25_is_the_ranking_when_using_is_left_out(tienda: Engine):
    query = "SELECT id, SCORE() FROM productos WHERE MATCH(texto, 'teclado usb')"
    assert tienda.execute(query).rows == tienda.execute(f"{query} USING BM25").rows
    assert "BM25 k1=1.2 b=0.75" in scan_detail(tienda.execute(query))


def test_the_bm25_parameters_change_the_ranking(tienda: Engine):
    query = "SELECT id, SCORE() FROM productos WHERE MATCH(texto, 'usb cable')"
    tuned = tienda.execute(f"{query} WITH (k1 = 0, b = 0)")
    assert tuned.rows != tienda.execute(query).rows
    assert "BM25 k1=0 b=0" in scan_detail(tuned)


def test_a_limit_reaches_the_top_k(tienda: Engine, config: EngineConfig):
    expected = oracle_ranking(tienda, config, "usb", RankingMethod.BM25)
    result = tienda.execute("SELECT id FROM productos WHERE MATCH(texto, 'usb') LIMIT 3")
    assert [row[0] for row in result.rows] == [identifier for identifier, _ in expected[:3]]
    assert "top 3" in scan_detail(result)
    assert "(filas=3," in scan_detail(result)


def test_an_offset_asks_the_top_k_for_the_rows_it_skips(tienda: Engine, config: EngineConfig):
    expected = oracle_ranking(tienda, config, "usb", RankingMethod.BM25)
    result = tienda.execute(
        "SELECT id, SCORE() AS s FROM productos WHERE MATCH(texto, 'usb') "
        "ORDER BY s DESC LIMIT 2 OFFSET 3"
    )
    assert_same_ranking(result.rows, expected[3:5])
    assert "top 5" in scan_detail(result)
    assert "ExternalSort" not in operations(result)


def test_without_a_limit_every_matching_row_comes_in_score_order(
    tienda: Engine, config: EngineConfig
):
    result = tienda.execute("SELECT id, SCORE() FROM productos WHERE MATCH(texto, 'usb')")
    assert_same_ranking(result.rows, oracle_ranking(tienda, config, "usb", RankingMethod.BM25))
    assert len(result.rows) == len([text for *_, text in PRODUCTS if text and "USB" in text])
    assert "a demanda" in scan_detail(result)


def test_a_residual_filter_reads_the_lazy_order_only_as_far_as_it_needs(
    tienda: Engine, config: EngineConfig
):
    prices = {identifier: price for identifier, _, price, _ in PRODUCTS}
    ranked = oracle_ranking(tienda, config, "usb", RankingMethod.BM25)
    cheap = [identifier for identifier, _ in ranked if prices[identifier] < 40]
    result = tienda.execute(
        "EXPLAIN ANALYZE SELECT id FROM productos WHERE MATCH(texto, 'usb') AND precio < 40 "
        "LIMIT 2"
    )
    assert operations(result) == ["Limit", "Projection", "Filter", "TextSearchScan"]
    read = cheap[1]
    assert f"(filas={[identifier for identifier, _ in ranked].index(read) + 1}," in scan_detail(
        result
    )
    rows = tienda.execute(
        "SELECT id FROM productos WHERE MATCH(texto, 'usb') AND precio < 40 LIMIT 2"
    ).rows
    assert [row[0] for row in rows] == cheap[:2]


def test_the_filter_does_not_evaluate_the_match_again(tienda: Engine):
    plan = tienda.execute(
        "EXPLAIN SELECT id FROM productos WHERE precio < 40 AND MATCH(texto, 'usb')"
    ).plan
    assert plan is not None
    filters = [line.strip("└─ ") for line in plan.render().splitlines() if "Filter" in line]
    assert filters == ["Filter: precio < 40"]
    alone = tienda.execute("EXPLAIN SELECT id FROM productos WHERE MATCH(texto, 'usb')")
    assert "Filter" not in operations(alone)


def test_ordering_by_score_ascending_sorts(tienda: Engine, config: EngineConfig):
    expected = oracle_ranking(tienda, config, "teclado usb", RankingMethod.BM25)
    result = tienda.execute(
        "SELECT id, SCORE() FROM productos WHERE MATCH(texto, 'teclado usb') ORDER BY SCORE()"
    )
    scores = [score for _, score in result.rows]
    assert scores == sorted(scores)
    assert {row[0] for row in result.rows} == {identifier for identifier, _ in expected}
    assert "ExternalSort" in operations(result)


@pytest.mark.parametrize("order", ["SCORE() DESC", "relevancia DESC", "2 DESC"])
def test_ordering_by_score_descending_does_not_sort_again(tienda: Engine, order: str):
    result = tienda.execute(
        "SELECT id, SCORE() AS relevancia FROM productos WHERE MATCH(texto, 'teclado usb') "
        f"ORDER BY {order} LIMIT 4"
    )
    assert "ExternalSort" not in operations(result)
    assert "top 4" in scan_detail(result)
    scores = [score for _, score in result.rows]
    assert scores == sorted(scores, reverse=True)


def test_ordering_by_another_column_sorts_and_reads_every_match(tienda: Engine):
    result = tienda.execute(
        "SELECT id FROM productos WHERE MATCH(texto, 'usb') ORDER BY precio LIMIT 2"
    )
    assert [row[0] for row in result.rows] == [2, 6]
    assert "a demanda" in scan_detail(result)


def test_a_score_without_alias_is_called_score_and_star_leaves_it_out(tienda: Engine):
    result = tienda.execute("SELECT *, SCORE() FROM productos WHERE MATCH(texto, 'funda')")
    assert result.columns == ("id", "marca", "precio", "texto", "score")
    assert result.rows[0][:-1] == PRODUCTS[3]


def test_the_score_can_be_filtered_computed_and_aggregated(tienda: Engine, config: EngineConfig):
    expected = dict(oracle_ranking(tienda, config, "usb", RankingMethod.BM25))
    threshold = sorted(expected.values())[len(expected) // 2]
    rows = tienda.execute(
        f"SELECT id, SCORE() * 2 FROM productos WHERE MATCH(texto, 'usb') AND SCORE() > "
        f"{threshold}"
    ).rows
    assert {identifier for identifier, _ in rows} == {
        identifier for identifier, score in expected.items() if score > threshold
    }
    for identifier, doubled in rows:
        assert doubled == pytest.approx(2 * expected[identifier], abs=TOLERANCE)
    grouped = tienda.execute(
        "SELECT marca, COUNT(*), MAX(SCORE()) FROM productos WHERE MATCH(texto, 'usb') "
        "GROUP BY marca ORDER BY marca"
    ).rows
    brands = {identifier: brand for identifier, brand, _, _ in PRODUCTS}
    for brand, count, best in grouped:
        mine = [score for identifier, score in expected.items() if brands[identifier] == brand]
        assert count == len(mine)
        assert best == pytest.approx(max(mine), abs=TOLERANCE)


def test_a_distinct_keeps_the_score_order(tienda: Engine, config: EngineConfig):
    brands = {identifier: brand for identifier, brand, _, _ in PRODUCTS}
    ranked = oracle_ranking(tienda, config, "usb", RankingMethod.BM25)
    expected = list(dict.fromkeys(brands[identifier] for identifier, _ in ranked))[:2]
    result = tienda.execute(
        "SELECT DISTINCT marca FROM productos WHERE MATCH(texto, 'usb') "
        "ORDER BY SCORE() DESC LIMIT 2"
    )
    assert [row[0] for row in result.rows] == expected
    assert "a demanda" in scan_detail(result)


def joined(tienda: Engine, config: EngineConfig, query: str) -> list[tuple[int, str, float]]:
    countries = dict(BRANDS)
    brands = {identifier: brand for identifier, brand, _, _ in PRODUCTS}
    return [
        (identifier, countries[brands[identifier]], score)
        for identifier, score in oracle_ranking(tienda, config, query, RankingMethod.BM25)
    ]


@pytest.mark.parametrize(
    "source",
    [
        "productos p JOIN marcas m ON p.marca = m.marca",
        "marcas m JOIN productos p ON p.marca = m.marca",
    ],
)
def test_a_match_on_one_side_of_a_join_feeds_that_side(
    tienda: Engine, config: EngineConfig, source: str
):
    result = tienda.execute(
        f"SELECT p.id, m.pais, SCORE() FROM {source} WHERE MATCH(p.texto, 'teclado usb') "
        "AND m.pais <> 'Perú' ORDER BY SCORE() DESC, p.id"
    )
    expected = [row for row in joined(tienda, config, "teclado usb") if row[1] != "Perú"]
    assert [row[:2] for row in result.rows] == [row[:2] for row in expected]
    for row, (_, _, score) in zip(result.rows, expected, strict=True):
        assert row[2] == pytest.approx(score, abs=TOLERANCE)
    assert "TextSearchScan" in operations(result)
    assert "a demanda" in scan_detail(result)


def test_a_match_column_must_name_its_table_when_both_have_it(tienda: Engine):
    tienda.execute("CREATE TABLE notas (id INT, texto TEXT)")
    with pytest.raises(AmbiguousColumnError):
        tienda.execute(
            "SELECT * FROM productos p JOIN notas n ON p.id = n.id WHERE MATCH(texto, 'usb')"
        )


def test_explain_tells_the_terms_and_their_df_before_running(tienda: Engine):
    result = tienda.execute(
        "EXPLAIN SELECT id FROM productos WHERE MATCH(texto, 'Teclados USB para zzzz') "
        "USING TF_IDF LIMIT 3"
    )
    assert result.plan is not None
    assert result.plan.children[0].children[0].detail == (
        "productos.texto MATCH 'Teclados USB para zzzz' · TF_IDF + coseno · top 3 "
        "(índice idx_texto, INVERTED) · términos: tecl (df 2), usb (df 6), zzzz (df 0)"
    )


def test_explain_analyze_adds_the_work_of_the_ranking(tienda: Engine):
    result = tienda.execute(
        "EXPLAIN ANALYZE SELECT id FROM productos WHERE MATCH(texto, 'teclado usb') LIMIT 3"
    )
    detail = scan_detail(result)
    assert "términos: tecl (df 2), usb (df 6) · 8 postings leídos, 7 documentos puntuados" in (
        detail
    )


def test_a_query_with_only_stopwords_finds_nothing(tienda: Engine):
    result = tienda.execute("SELECT id FROM productos WHERE MATCH(texto, 'de la para con')")
    assert result.rows == ()
    assert "términos: ninguno" in scan_detail(result)


def test_the_result_tells_the_interface_what_to_highlight(tienda: Engine):
    result = tienda.execute(
        "SELECT id FROM productos WHERE MATCH(texto, 'Teclados inalámbricos') USING TF_IDF"
    )
    assert result.text is not None
    assert (result.text.table, result.text.column) == ("productos", "texto")
    assert result.text.query == "Teclados inalámbricos"
    assert result.text.method is RankingMethod.TF_IDF
    assert result.text.terms == ("tecl", "inalambr")
    assert tienda.execute("SELECT id FROM productos").text is None


@pytest.mark.parametrize(
    ("condition", "message"),
    [
        ("MATCH(texto, 'usb') OR precio < 10", "no se puede negar ni combinar con OR"),
        ("NOT MATCH(texto, 'usb')", "no se puede negar ni combinar con OR"),
        ("MATCH(texto, 'usb') AND MATCH(texto, 'cable')", "un solo MATCH"),
        ("MATCH(precio, 'usb')", "busca en una columna de texto"),
        ("MATCH(texto, marca)", "texto constante"),
        ("MATCH(texto, 5)", "texto constante"),
        ("MATCH(texto, NULL)", "texto constante"),
        ("MATCH(texto)", "recibe la columna y la consulta"),
        ("MATCH(texto, 'usb', modo='todas')", "recibe la columna y la consulta"),
        ("MATCH('usb', texto)", "es la columna en la que se busca"),
    ],
)
def test_a_match_that_cannot_be_answered_fails_before_reading_rows(
    tienda: Engine, condition: str, message: str
):
    tienda.execute("DELETE FROM productos")
    with pytest.raises(TextSearchError, match=message):
        tienda.execute(f"SELECT id FROM productos WHERE {condition}")


def test_a_match_without_an_inverted_index_says_how_to_create_it(tienda: Engine):
    tienda.execute("DROP INDEX idx_texto")
    with pytest.raises(TextSearchError, match=r"CREATE INDEX ON productos USING INVERTED \(texto\)"):
        tienda.execute("SELECT id FROM productos WHERE MATCH(texto, 'usb')")


def test_a_match_on_an_unknown_column_says_so(tienda: Engine):
    with pytest.raises(UnknownColumnError):
        tienda.execute("SELECT id FROM productos WHERE MATCH(cuerpo, 'usb')")


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT MATCH(texto, 'usb') FROM productos",
        "SELECT id FROM productos WHERE MATCH(texto, 'usb') ORDER BY MATCH(texto, 'cable')",
        "UPDATE productos SET precio = 0 WHERE MATCH(texto, 'usb')",
        "DELETE FROM productos WHERE MATCH(texto, 'usb')",
    ],
)
def test_a_match_anywhere_else_says_where_it_goes(tienda: Engine, statement: str):
    with pytest.raises(ExpressionError, match="va en el WHERE de un SELECT"):
        tienda.execute(statement)
    assert tienda.execute("SELECT COUNT(*) FROM productos").rows == ((len(PRODUCTS),),)


@pytest.mark.parametrize(
    ("statement", "error", "message"),
    [
        ("SELECT id, SCORE() FROM productos", TextSearchError, "no tiene ninguno"),
        ("SELECT id FROM productos ORDER BY SCORE()", TextSearchError, "no tiene ninguno"),
        (
            "SELECT SCORE(id) FROM productos WHERE MATCH(texto, 'usb')",
            TextSearchError,
            "no recibe argumentos",
        ),
        (
            "SELECT id, SCORE() FROM productos WHERE MATCH(texto, 'usb') GROUP BY id",
            UnknownColumnError,
            "score",
        ),
    ],
)
def test_a_score_needs_the_match_of_its_select(
    tienda: Engine, statement: str, error: type, message: str
):
    with pytest.raises(error, match=message):
        tienda.execute(statement)


@pytest.mark.parametrize(
    ("suffix", "error", "message"),
    [
        ("USING TF_IDF WITH (k1 = 1.5)", RankingParameterError, "TF-IDF no tiene parámetros"),
        ("USING HNSW", TextSearchError, "USING TF_IDF o USING BM25, no con HNSW"),
        ("WITH (k1 = -1)", RankingParameterError, "k1"),
        ("WITH (b = 2)", RankingParameterError, "b"),
        ("WITH (k1 = 'mucho')", TextSearchError, "k1 es un número"),
        ("WITH (metric = cosine)", TextSearchError, "k1 y b; no metric"),
    ],
)
def test_a_ranking_that_does_not_fit_fails_before_reading_rows(
    tienda: Engine, suffix: str, error: type, message: str
):
    with pytest.raises(error, match=message):
        tienda.execute(f"EXPLAIN SELECT id FROM productos WHERE MATCH(texto, 'usb') {suffix}")


def test_using_without_a_match_is_still_rejected(tienda: Engine):
    with pytest.raises(UnsupportedQueryError, match="no tiene un MATCH en su WHERE"):
        tienda.execute("SELECT id FROM productos WHERE precio < 10 USING BM25")


def found_ids(engine: Engine, query: str) -> list[int]:
    rows = engine.execute(f"SELECT id FROM productos WHERE MATCH(texto, '{query}')").rows
    return sorted(row[0] for row in rows)


@pytest.mark.parametrize(
    "new",
    [
        "Lámpara de escritorio blanca para leer",
        "Lámpara de escritorio con brazo articulado, base de metal y luz cálida " * 8,
        "x",
        None,
    ],
    ids=["mismo largo", "más largo", "más corto", "nulo"],
)
def test_updating_the_indexed_text_searches_the_new_one(tienda: Engine, new: str | None):
    """El índice quita el documento viejo antes de que el texto nuevo ocupe su lugar en el
    archivo de valores, o de que ese lugar se libere: lo encuentra por la dirección de la
    fila, y la tabla le pregunta si la columna cambió con los valores lógicos de antes."""
    tienda.execute(f"UPDATE productos SET texto = {literal(new)} WHERE id = 4")
    index = tienda.table("productos").text_search_index("idx_texto").inverted_index
    assert found_ids(tienda, "funda cuero") == []
    assert found_ids(tienda, "lámpara escritorio") == ([9] if new in (None, "x") else [4, 9])
    texts = [text for *_, text in PRODUCTS if text is not None]
    assert index.document_count == len(texts) - (new is None)
    assert index.deleted_count == 1
    original = literal(PRODUCTS[3][TEXT_POSITION])
    tienda.execute(f"UPDATE productos SET texto = {original} WHERE id = 4")
    assert found_ids(tienda, "funda cuero") == [4]


def test_a_rollback_leaves_the_same_matches(engine: Engine, config: EngineConfig):
    with Session(engine, LockManager(config), TransactionManager()) as session:
        session.execute(
            "CREATE TABLE productos (id INT PRIMARY KEY, marca VARCHAR(10), precio INT, "
            "texto TEXT)"
        )
        insert(engine, "productos", PRODUCTS)
        session.execute("CREATE INDEX idx_texto ON productos USING INVERTED (texto)")
        before = {query: found_ids(engine, query) for query in QUERIES}
        session.execute("BEGIN")
        session.execute("INSERT INTO productos VALUES (11, 'Logi', 5, 'Teclado USB lavable')")
        session.execute("UPDATE productos SET texto = 'Funda impermeable' WHERE id = 2")
        session.execute("DELETE FROM productos WHERE id IN (3, 5)")
        inside = session.execute(
            "SELECT id FROM productos WHERE MATCH(texto, 'teclado usb') ORDER BY id"
        ).rows
        assert inside == ((1,), (6,), (8,), (9,), (11,))
        session.execute("ROLLBACK")
        assert {query: found_ids(engine, query) for query in QUERIES} == before


def test_a_commit_leaves_no_document_waiting_in_memory(engine: Engine, config: EngineConfig):
    engine.execute("CREATE TABLE notas (id INT PRIMARY KEY, texto TEXT)")
    engine.execute("CREATE INDEX i ON notas USING INVERTED (texto)")
    with Session(engine, LockManager(config), TransactionManager()) as session:
        session.execute("BEGIN")
        session.execute("INSERT INTO notas VALUES (1, 'árbol B+ agrupado')")
        index = engine.table("notas").text_search_index("i").inverted_index
        assert index.pending_document_count == 1
        session.execute("COMMIT")
        assert index.pending_document_count == 0


def test_reopening_the_engine_answers_the_same(config: EngineConfig):
    query = "SELECT id, SCORE() FROM productos WHERE MATCH(texto, 'cable usb') USING TF_IDF"
    with Engine(config) as engine:
        engine.execute(
            "CREATE TABLE productos (id INT PRIMARY KEY, marca VARCHAR(10), precio INT, "
            "texto TEXT INDEX INVERTED)"
        )
        insert(engine, "productos", PRODUCTS)
        before = engine.execute(query).rows
    with Engine(config) as engine:
        assert engine.execute(query).rows == before
