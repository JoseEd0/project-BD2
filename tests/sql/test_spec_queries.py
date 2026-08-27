"""Every SQL example of `Proyecto_Integrador_BD2.md`, parsed verbatim."""

from sql import parse
from sql.nodes import (
    BinaryOperation,
    BinaryOperator,
    ColumnRef,
    FunctionCall,
    IndexType,
    Literal,
    RankingMethod,
    SelectStatement,
    SortDirection,
    Star,
)


def select(source: str) -> SelectStatement:
    statement = parse(source)
    assert isinstance(statement, SelectStatement)
    return statement


def test_relational_examples():
    assert select("SELECT * FROM tabla WHERE id = 1").where is not None
    assert select("SELECT * FROM tabla ORDER BY nombre").order_by
    assert select("SELECT * FROM tabla GROUP BY categoria").group_by
    assert parse("INSERT INTO tabla VALUES (1, 'a', 2.5)")
    assert parse("DELETE FROM tabla WHERE id = 1")


def test_spatial_range_query():
    statement = select(
        "SELECT * FROM tiendas "
        "WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000;"
    )
    condition = statement.where
    assert isinstance(condition, BinaryOperation)
    assert condition.operator is BinaryOperator.LESS
    assert isinstance(condition.left, FunctionCall)
    assert condition.left.name == "distancia"
    assert condition.right == Literal(5000)


def test_spatial_knn_query():
    statement = select(
        "SELECT * FROM restaurantes ORDER BY distancia(ubicacion, mi_ubicacion) LIMIT 10;"
    )
    assert isinstance(statement.order_by[0].expression, FunctionCall)
    assert statement.limit == 10


def test_full_text_tf_idf_query():
    statement = select(
        "SELECT * FROM documentos WHERE MATCH(contenido, 'base de datos vectorial')\n"
        "    USING TF_IDF LIMIT 10;"
    )
    match = statement.where
    assert isinstance(match, FunctionCall)
    assert match.arguments == (ColumnRef("contenido"), Literal("base de datos vectorial"))
    assert statement.search_method is RankingMethod.TF_IDF
    assert statement.limit == 10


def test_full_text_bm25_query_with_score_alias():
    statement = select(
        "SELECT *, SCORE() as relevancia FROM articulos\n"
        "    WHERE MATCH(texto, 'machine learning')\n"
        "    USING BM25\n"
        "    ORDER BY relevancia DESC;"
    )
    assert statement.projections[0].expression == Star()
    assert statement.projections[1] .alias == "relevancia"
    assert statement.projections[1].expression == FunctionCall(name="SCORE")
    assert statement.search_method is RankingMethod.BM25
    assert statement.order_by[0].expression == ColumnRef("relevancia")
    assert statement.order_by[0].direction is SortDirection.DESCENDING


def test_multimedia_image_similarity_query():
    statement = select(
        "SELECT * FROM imagenes\n"
        "    WHERE SIMILAR_TO('foto_consulta.jpg', k=10)\n"
        "    USING HNSW WITH METRIC=cosine;"
    )
    call = statement.where
    assert isinstance(call, FunctionCall)
    assert call.arguments == (Literal("foto_consulta.jpg"),)
    assert dict(call.keyword_arguments) == {"k": Literal(10)}
    assert statement.search_method is IndexType.HNSW
    assert statement.options == {"metric": "cosine"}


def test_multimedia_audio_similarity_query():
    statement = select(
        "SELECT nombre, artista, SIMILARITY_SCORE() as score\n"
        "    FROM canciones\n"
        "    WHERE SIMILAR_TO('audio_query.mp3', k=5)\n"
        "    USING IVF WITH METRIC=euclidean\n"
        "    ORDER BY score DESC;"
    )
    assert [projection.alias for projection in statement.projections] == [None, None, "score"]
    assert statement.search_method is IndexType.IVF
    assert statement.options == {"metric": "euclidean"}
    assert statement.order_by[0].direction is SortDirection.DESCENDING


def test_polygon_intersection_query():
    statement = select(
        "SELECT * FROM sucursales "
        "WHERE INTERSECTS(ubicacion, POLYGON((0, 0), (0, 1), (1, 1)))"
    )
    call = statement.where
    assert isinstance(call, FunctionCall)
    assert call.name == "INTERSECTS"
