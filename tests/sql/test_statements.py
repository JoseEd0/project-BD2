import pytest

from sql import parse, parse_script
from sql.errors import SqlSyntaxError
from sql.nodes import (
    Assignment,
    BeginTransactionStatement,
    ColumnRef,
    CommitTransactionStatement,
    CreateIndexStatement,
    CreateTableFromFileStatement,
    CreateTableStatement,
    DataType,
    DataTypeKind,
    DeleteStatement,
    DropIndexStatement,
    DropTableStatement,
    IndexType,
    InsertStatement,
    JoinKind,
    Literal,
    RankingMethod,
    RollbackTransactionStatement,
    SelectStatement,
    SortDirection,
    Star,
    UpdateStatement,
)


def select(source: str) -> SelectStatement:
    statement = parse(source)
    assert isinstance(statement, SelectStatement)
    return statement


def test_minimal_select():
    statement = select("SELECT * FROM alumnos")
    assert statement.projections[0].expression == Star()
    assert statement.source.name == "alumnos"
    assert statement.where is None


def test_select_is_case_insensitive_and_ignores_trailing_semicolon():
    assert select("select * from t;").source.name == "t"


def test_select_distinct():
    assert select("SELECT DISTINCT city FROM t").distinct


def test_projection_aliases_explicit_and_implicit():
    statement = select("SELECT a AS x, b y FROM t")
    assert [projection.alias for projection in statement.projections] == ["x", "y"]


def test_table_alias():
    statement = select("SELECT * FROM alumnos AS a")
    assert statement.source.alias == "a"


def test_inner_join():
    statement = select("SELECT * FROM a JOIN b ON a.id = b.id")
    assert statement.joins[0].kind is JoinKind.INNER
    assert statement.joins[0].table.name == "b"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("INNER JOIN", JoinKind.INNER),
        ("LEFT JOIN", JoinKind.LEFT),
        ("LEFT OUTER JOIN", JoinKind.LEFT),
        ("RIGHT JOIN", JoinKind.RIGHT),
        ("FULL OUTER JOIN", JoinKind.FULL),
    ],
)
def test_join_kinds(source: str, expected: JoinKind):
    statement = select(f"SELECT * FROM a {source} b ON a.id = b.id")
    assert statement.joins[0].kind is expected


def test_several_joins():
    statement = select("SELECT * FROM a JOIN b ON a.id = b.id LEFT JOIN c ON b.id = c.id")
    assert len(statement.joins) == 2


def test_group_by_and_having():
    statement = select("SELECT city, COUNT(*) FROM t GROUP BY city HAVING COUNT(*) > 2")
    assert statement.group_by == (ColumnRef("city"),)
    assert statement.having is not None


def test_order_by_directions():
    statement = select("SELECT * FROM t ORDER BY a, b DESC, c ASC")
    assert [item.direction for item in statement.order_by] == [
        SortDirection.ASCENDING,
        SortDirection.DESCENDING,
        SortDirection.ASCENDING,
    ]


def test_limit_and_offset():
    statement = select("SELECT * FROM t LIMIT 10 OFFSET 5")
    assert (statement.limit, statement.offset) == (10, 5)


def test_limit_requires_an_integer():
    with pytest.raises(SqlSyntaxError, match="row count"):
        parse("SELECT * FROM t LIMIT x")


def test_missing_from_is_rejected():
    with pytest.raises(SqlSyntaxError, match="FROM"):
        parse("SELECT *")


def test_using_resolves_index_and_ranking_methods():
    assert select("SELECT * FROM t USING HNSW").search_method is IndexType.HNSW
    assert select("SELECT * FROM t USING bm25").search_method is RankingMethod.BM25


def test_unknown_using_method_is_rejected():
    with pytest.raises(SqlSyntaxError, match="unknown access method"):
        parse("SELECT * FROM t USING MAGIC")


def test_with_options_without_parentheses():
    statement = select("SELECT * FROM t USING IVF WITH METRIC = cosine, nprobe = 8")
    assert statement.options == {"metric": "cosine", "nprobe": 8}


def test_with_options_with_parentheses_and_negative_number():
    statement = select("SELECT * FROM t WITH (threshold = -0.5, exact = TRUE)")
    assert statement.options == {"threshold": -0.5, "exact": True}


def test_duplicate_option_is_rejected():
    with pytest.raises(SqlSyntaxError, match="duplicate option"):
        parse("SELECT * FROM t WITH (k = 1, k = 2)")


def test_insert_single_row():
    statement = parse("INSERT INTO t VALUES (1, 'a')")
    assert statement == InsertStatement(
        table="t", rows=((Literal(1), Literal("a")),), columns=None
    )


def test_insert_with_column_list_and_several_rows():
    statement = parse("INSERT INTO t (a, b) VALUES (1, 2), (3, 4)")
    assert isinstance(statement, InsertStatement)
    assert statement.columns == ("a", "b")
    assert len(statement.rows) == 2


def test_insert_arity_mismatch_is_rejected():
    with pytest.raises(SqlSyntaxError, match="expected 2 values"):
        parse("INSERT INTO t (a, b) VALUES (1)")


def test_update_with_where():
    statement = parse("UPDATE t SET a = 1, b = a + 1 WHERE id = 7")
    assert isinstance(statement, UpdateStatement)
    assert statement.assignments[0] == Assignment(column="a", value=Literal(1))
    assert statement.where is not None


def test_delete_all_and_delete_where():
    assert parse("DELETE FROM t") == DeleteStatement(table="t")
    statement = parse("DELETE FROM t WHERE id = 1")
    assert isinstance(statement, DeleteStatement)
    assert statement.where is not None


def test_create_table_with_constraints():
    statement = parse(
        "CREATE TABLE alumnos ("
        "  id INT PRIMARY KEY,"
        "  nombre VARCHAR(50) NOT NULL,"
        "  correo VARCHAR(80) UNIQUE INDEX HASH,"
        "  puntaje FLOAT"
        ")"
    )
    assert isinstance(statement, CreateTableStatement)
    identifier, nombre, correo, puntaje = statement.columns
    assert identifier.primary_key and not identifier.nullable
    assert nombre.data_type == DataType(DataTypeKind.VARCHAR, 50)
    assert not nombre.nullable
    assert correo.unique and correo.index is IndexType.HASH
    assert puntaje.data_type == DataType(DataTypeKind.FLOAT)
    assert puntaje.nullable


def test_create_table_if_not_exists():
    statement = parse("CREATE TABLE IF NOT EXISTS t (id INT)")
    assert isinstance(statement, CreateTableStatement)
    assert statement.if_not_exists


def test_table_level_primary_key():
    statement = parse("CREATE TABLE t (id INT, nombre TEXT, PRIMARY KEY (id))")
    assert isinstance(statement, CreateTableStatement)
    assert statement.columns[0].primary_key
    assert not statement.columns[1].primary_key


def test_table_level_primary_key_on_unknown_column_is_rejected():
    with pytest.raises(SqlSyntaxError, match="unknown column"):
        parse("CREATE TABLE t (id INT, PRIMARY KEY (missing))")


def test_spatial_and_vector_column_types():
    statement = parse("CREATE TABLE t (ubicacion POINT, features VECTOR(128))")
    assert isinstance(statement, CreateTableStatement)
    assert statement.columns[0].data_type == DataType(DataTypeKind.POINT)
    assert statement.columns[1].data_type == DataType(DataTypeKind.VECTOR, 128)


def test_vector_without_dimension_is_rejected():
    with pytest.raises(SqlSyntaxError, match="requires a size"):
        parse("CREATE TABLE t (features VECTOR)")


def test_size_on_a_type_that_does_not_take_one_is_rejected():
    with pytest.raises(SqlSyntaxError, match="does not take a size"):
        parse("CREATE TABLE t (id INT(11))")


def test_zero_size_is_rejected():
    with pytest.raises(SqlSyntaxError, match="greater than zero"):
        parse("CREATE TABLE t (name VARCHAR(0))")


def test_unknown_column_type_is_rejected():
    with pytest.raises(SqlSyntaxError, match="unknown column type"):
        parse("CREATE TABLE t (id NUMERIC)")


def test_create_table_from_file_with_index():
    statement = parse('CREATE TABLE Restaurantes FROM FILE "datos/restaurantes.csv" '
                      'USING INDEX BTREE("id")')
    assert isinstance(statement, CreateTableFromFileStatement)
    assert statement.path == "datos/restaurantes.csv"
    assert statement.index is not None
    assert statement.index.method is IndexType.BTREE
    assert statement.index.columns == ("id",)


def test_create_table_from_file_without_index():
    statement = parse("CREATE TABLE t FROM FILE 'data.csv'")
    assert isinstance(statement, CreateTableFromFileStatement)
    assert statement.index is None


def test_create_index_with_name_and_options():
    statement = parse("CREATE INDEX idx_vec ON fotos USING HNSW (features) WITH (M = 16, ef = 200)")
    assert isinstance(statement, CreateIndexStatement)
    assert statement.name == "idx_vec"
    assert statement.table == "fotos"
    assert statement.spec.method is IndexType.HNSW
    assert statement.spec.options == {"m": 16, "ef": 200}


def test_create_index_without_name():
    statement = parse("CREATE INDEX ON t USING RTREE (ubicacion)")
    assert isinstance(statement, CreateIndexStatement)
    assert statement.name is None


def test_unknown_index_method_is_rejected():
    with pytest.raises(SqlSyntaxError, match="unknown index method"):
        parse("CREATE INDEX ON t USING QUADTREE (a)")


def test_drop_statements():
    assert parse("DROP TABLE IF EXISTS t") == DropTableStatement(name="t", if_exists=True)
    assert parse("DROP INDEX idx ON t") == DropIndexStatement(name="idx", table="t")


def test_transaction_statements():
    assert parse("BEGIN TRANSACTION") == BeginTransactionStatement()
    assert parse("BEGIN") == BeginTransactionStatement()
    assert parse("END TRANSACTION") == CommitTransactionStatement()
    assert parse("COMMIT") == CommitTransactionStatement()
    assert parse("ROLLBACK") == RollbackTransactionStatement()


def test_script_with_several_statements():
    statements = parse_script(
        "BEGIN TRANSACTION;\n"
        "INSERT INTO t VALUES (1);\n"
        "DELETE FROM t WHERE id = 1;\n"
        "END TRANSACTION;"
    )
    assert len(statements) == 4


def test_script_ignores_blank_statements_and_comments():
    statements = parse_script(";; -- nothing\n;")
    assert statements == []


def test_parse_rejects_more_than_one_statement():
    with pytest.raises(SqlSyntaxError, match="single statement"):
        parse("SELECT * FROM a; SELECT * FROM b")


def test_missing_semicolon_between_statements_is_rejected():
    with pytest.raises(SqlSyntaxError, match="';'"):
        parse_script("SELECT * FROM a SELECT * FROM b")


def test_empty_input_is_rejected_by_parse():
    with pytest.raises(SqlSyntaxError, match="expected a statement"):
        parse("   ")


def test_unknown_statement_is_rejected():
    with pytest.raises(SqlSyntaxError, match="expected a statement"):
        parse("TRUNCATE t")


def test_duplicate_column_in_create_table_is_rejected():
    with pytest.raises(SqlSyntaxError, match="duplicate column 'id'"):
        parse("CREATE TABLE t (id INT, id TEXT)")


def test_duplicate_column_is_detected_case_insensitively():
    with pytest.raises(SqlSyntaxError, match="duplicate column"):
        parse("CREATE TABLE t (id INT, ID TEXT)")


def test_two_table_level_primary_keys_are_rejected():
    with pytest.raises(SqlSyntaxError, match="already declares a PRIMARY KEY"):
        parse("CREATE TABLE t (a INT, b INT, PRIMARY KEY (a), PRIMARY KEY (b))")


def test_duplicate_column_in_primary_key_is_rejected():
    with pytest.raises(SqlSyntaxError, match="duplicate column"):
        parse("CREATE TABLE t (a INT, PRIMARY KEY (a, a))")


def test_duplicate_column_in_insert_list_is_rejected():
    with pytest.raises(SqlSyntaxError, match="duplicate column"):
        parse("INSERT INTO t (a, a) VALUES (1, 2)")


def test_duplicate_column_in_index_is_rejected():
    with pytest.raises(SqlSyntaxError, match="duplicate column"):
        parse("CREATE INDEX ON t USING BTREE (a, a)")


def test_column_assigned_twice_in_update_is_rejected():
    with pytest.raises(SqlSyntaxError, match="assigned twice"):
        parse("UPDATE t SET a = 1, a = 2")


def test_composite_primary_key_marks_every_column():
    statement = parse("CREATE TABLE t (a INT, b INT, PRIMARY KEY (a, b))")
    assert isinstance(statement, CreateTableStatement)
    assert all(column.primary_key for column in statement.columns)


def test_parsed_options_are_immutable():
    statement = select("SELECT * FROM t WITH (metric = cosine)")
    with pytest.raises(TypeError):
        statement.options["metric"] = "euclidean"  # type: ignore[index]


def test_insert_rows_must_share_the_same_arity():
    with pytest.raises(SqlSyntaxError, match="expected 2 values per row, got 1"):
        parse("INSERT INTO t VALUES (1, 2), (3)")


def test_second_statement_error_points_at_the_extra_statement():
    with pytest.raises(SqlSyntaxError) as error:
        parse("SELECT * FROM a; SELECT * FROM b")
    assert error.value.column == 18
