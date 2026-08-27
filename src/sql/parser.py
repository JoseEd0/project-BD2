"""Recursive-descent parser for the SQL dialect of the engine.

The grammar covers the relational core plus the spatial, full-text and
multimedia extensions required by the project: `USING <method>` selects the
access structure and `WITH <options>` carries its tuning parameters.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import MappingProxyType

from .errors import SqlSyntaxError
from .lexer import tokenize
from .nodes import (
    EMPTY_OPTIONS,
    Assignment,
    BeginTransactionStatement,
    BetweenPredicate,
    BinaryOperation,
    BinaryOperator,
    ColumnDefinition,
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
    Expression,
    FunctionCall,
    IndexSpec,
    IndexType,
    InPredicate,
    InsertStatement,
    Join,
    JoinKind,
    LikePredicate,
    Literal,
    NullPredicate,
    OptionValue,
    OrderItem,
    Projection,
    RankingMethod,
    RollbackTransactionStatement,
    SearchMethod,
    SelectStatement,
    SortDirection,
    Star,
    Statement,
    TableRef,
    TupleExpression,
    UnaryOperation,
    UnaryOperator,
    UpdateStatement,
)
from .tokens import Token, TokenType

# Parenthesis, NOT and unary-minus nesting all recurse; the cap keeps a pathological
# query inside the interpreter stack so it fails as a syntax error, not a crash.
MAX_EXPRESSION_DEPTH = 32

COMPARISON_OPERATORS: dict[TokenType, BinaryOperator] = {
    TokenType.EQUAL: BinaryOperator.EQUAL,
    TokenType.NOT_EQUAL: BinaryOperator.NOT_EQUAL,
    TokenType.LESS: BinaryOperator.LESS,
    TokenType.LESS_EQUAL: BinaryOperator.LESS_EQUAL,
    TokenType.GREATER: BinaryOperator.GREATER,
    TokenType.GREATER_EQUAL: BinaryOperator.GREATER_EQUAL,
}

ADDITIVE_OPERATORS: dict[TokenType, BinaryOperator] = {
    TokenType.PLUS: BinaryOperator.ADD,
    TokenType.MINUS: BinaryOperator.SUBTRACT,
}

MULTIPLICATIVE_OPERATORS: dict[TokenType, BinaryOperator] = {
    TokenType.STAR: BinaryOperator.MULTIPLY,
    TokenType.SLASH: BinaryOperator.DIVIDE,
    TokenType.PERCENT: BinaryOperator.MODULO,
}

DATA_TYPES: dict[str, DataTypeKind] = {
    "INT": DataTypeKind.INT,
    "INTEGER": DataTypeKind.INT,
    "BIGINT": DataTypeKind.BIGINT,
    "FLOAT": DataTypeKind.FLOAT,
    "REAL": DataTypeKind.FLOAT,
    "DOUBLE": DataTypeKind.DOUBLE,
    "BOOL": DataTypeKind.BOOLEAN,
    "BOOLEAN": DataTypeKind.BOOLEAN,
    "CHAR": DataTypeKind.CHAR,
    "VARCHAR": DataTypeKind.VARCHAR,
    "TEXT": DataTypeKind.TEXT,
    "DATE": DataTypeKind.DATE,
    "POINT": DataTypeKind.POINT,
    "VECTOR": DataTypeKind.VECTOR,
    "ARRAY": DataTypeKind.VECTOR,
    "BLOB": DataTypeKind.BLOB,
}

TYPES_WITH_OPTIONAL_SIZE = frozenset({DataTypeKind.CHAR, DataTypeKind.VARCHAR})
TYPES_WITH_REQUIRED_SIZE = frozenset({DataTypeKind.VECTOR})

INDEX_METHODS: dict[str, IndexType] = {
    "SEQ": IndexType.SEQUENTIAL,
    "SEQUENTIAL": IndexType.SEQUENTIAL,
    "BTREE": IndexType.BTREE,
    "BPTREE": IndexType.BTREE,
    "BPLUSTREE": IndexType.BTREE,
    "HASH": IndexType.HASH,
    "EHASH": IndexType.HASH,
    "EXTENDIBLE_HASH": IndexType.HASH,
    "RTREE": IndexType.RTREE,
    "INVERTED": IndexType.INVERTED,
    "SPIMI": IndexType.INVERTED,
    "IVF": IndexType.IVF,
    "HNSW": IndexType.HNSW,
}

RANKING_METHODS: dict[str, RankingMethod] = {
    "TF_IDF": RankingMethod.TF_IDF,
    "TFIDF": RankingMethod.TF_IDF,
    "BM25": RankingMethod.BM25,
}

NAME_TOKENS = frozenset({TokenType.IDENTIFIER, TokenType.QUOTED_NAME})
STRING_TOKENS = frozenset({TokenType.STRING, TokenType.QUOTED_NAME})

_OUTER_JOIN_KINDS: dict[TokenType, JoinKind] = {
    TokenType.LEFT: JoinKind.LEFT,
    TokenType.RIGHT: JoinKind.RIGHT,
    TokenType.FULL: JoinKind.FULL,
}

_TRANSACTION_STARTERS = frozenset(
    {TokenType.BEGIN, TokenType.END, TokenType.COMMIT, TokenType.ROLLBACK}
)


class Parser:
    """Builds an AST from SQL text.

    Raises:
        SqlLexicalError: when the input cannot be tokenized.
        SqlSyntaxError: when the tokens do not match the grammar.
    """

    def __init__(self, source: str) -> None:
        self._source = source
        self._tokens = tokenize(source)
        self._index = 0
        self._depth = 0

    def parse_script(self) -> list[Statement]:
        statements: list[Statement] = []
        while True:
            self._skip_separators()
            if self._check(TokenType.EOF):
                return statements
            statements.append(self._parse_statement())
            if not self._check(TokenType.EOF) and not self._check(TokenType.SEMICOLON):
                raise self._error_here("expected ';' between statements")

    def parse_single(self) -> Statement:
        self._skip_separators()
        if self._check(TokenType.EOF):
            raise self._error_here("expected a statement")
        statement = self._parse_statement()
        self._skip_separators()
        if not self._check(TokenType.EOF):
            raise self._error_here("expected a single statement")
        return statement

    def _skip_separators(self) -> None:
        while self._match(TokenType.SEMICOLON):
            pass

    # --- statement dispatch -------------------------------------------------

    def _parse_statement(self) -> Statement:
        token = self._peek()
        if token.type is TokenType.SELECT:
            return self._parse_select()
        if token.type is TokenType.INSERT:
            return self._parse_insert()
        if token.type is TokenType.UPDATE:
            return self._parse_update()
        if token.type is TokenType.DELETE:
            return self._parse_delete()
        if token.type is TokenType.CREATE:
            return self._parse_create()
        if token.type is TokenType.DROP:
            return self._parse_drop()
        if token.type in _TRANSACTION_STARTERS:
            return self._parse_transaction()
        raise self._error_here("expected a statement")

    # --- SELECT -------------------------------------------------------------

    def _parse_select(self) -> SelectStatement:
        self._expect(TokenType.SELECT)
        distinct = self._match(TokenType.DISTINCT)
        projections = self._parse_projections()
        self._expect(TokenType.FROM)
        source = self._parse_table_ref()
        joins = self._parse_joins()
        where = self._parse_optional_predicate(TokenType.WHERE)
        group_by = self._parse_group_by()
        having = self._parse_optional_predicate(TokenType.HAVING)
        search_method = self._parse_search_method()
        options = self._parse_with_options()
        order_by = self._parse_order_by()
        limit, offset = self._parse_limit_offset()
        return SelectStatement(
            projections=projections,
            source=source,
            distinct=distinct,
            joins=joins,
            where=where,
            group_by=group_by,
            having=having,
            search_method=search_method,
            options=options,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

    def _parse_projections(self) -> tuple[Projection, ...]:
        projections = [self._parse_projection()]
        while self._match(TokenType.COMMA):
            projections.append(self._parse_projection())
        return tuple(projections)

    def _parse_projection(self) -> Projection:
        star = self._parse_optional_star()
        if star is not None:
            return Projection(expression=star)
        expression = self._parse_expression()
        return Projection(expression=expression, alias=self._parse_optional_alias())

    def _parse_optional_star(self) -> Star | None:
        if self._match(TokenType.STAR):
            return Star()
        if not self._check_any(NAME_TOKENS):
            return None
        if not self._check(TokenType.DOT, offset=1) or not self._check(TokenType.STAR, offset=2):
            return None
        qualifier = self._advance().text
        self._advance()
        self._advance()
        return Star(qualifier=qualifier)

    def _parse_optional_alias(self) -> str | None:
        if self._match(TokenType.AS):
            return self._expect_name("an alias")
        if self._check_any(NAME_TOKENS):
            return self._advance().text
        return None

    def _parse_table_ref(self) -> TableRef:
        name = self._expect_name("a table name")
        return TableRef(name=name, alias=self._parse_optional_alias())

    def _parse_joins(self) -> tuple[Join, ...]:
        joins: list[Join] = []
        while True:
            kind = self._parse_optional_join_kind()
            if kind is None:
                return tuple(joins)
            table = self._parse_table_ref()
            self._expect(TokenType.ON)
            joins.append(Join(kind=kind, table=table, condition=self._parse_expression()))

    def _parse_optional_join_kind(self) -> JoinKind | None:
        if self._match(TokenType.JOIN):
            return JoinKind.INNER
        if self._match(TokenType.INNER):
            self._expect(TokenType.JOIN)
            return JoinKind.INNER
        outer_kind = _OUTER_JOIN_KINDS.get(self._peek().type)
        if outer_kind is None:
            return None
        self._advance()
        self._match(TokenType.OUTER)
        self._expect(TokenType.JOIN)
        return outer_kind

    def _parse_optional_predicate(self, keyword: TokenType) -> Expression | None:
        if not self._match(keyword):
            return None
        return self._parse_expression()

    def _parse_group_by(self) -> tuple[Expression, ...]:
        if not self._match(TokenType.GROUP):
            return ()
        self._expect(TokenType.BY)
        expressions = [self._parse_expression()]
        while self._match(TokenType.COMMA):
            expressions.append(self._parse_expression())
        return tuple(expressions)

    def _parse_order_by(self) -> tuple[OrderItem, ...]:
        if not self._match(TokenType.ORDER):
            return ()
        self._expect(TokenType.BY)
        items = [self._parse_order_item()]
        while self._match(TokenType.COMMA):
            items.append(self._parse_order_item())
        return tuple(items)

    def _parse_order_item(self) -> OrderItem:
        expression = self._parse_expression()
        if self._match(TokenType.DESC):
            return OrderItem(expression=expression, direction=SortDirection.DESCENDING)
        self._match(TokenType.ASC)
        return OrderItem(expression=expression)

    def _parse_limit_offset(self) -> tuple[int | None, int | None]:
        limit = self._parse_count(TokenType.LIMIT)
        offset = self._parse_count(TokenType.OFFSET)
        return limit, offset

    def _parse_count(self, keyword: TokenType) -> int | None:
        if not self._match(keyword):
            return None
        token = self._expect(TokenType.INTEGER, f"a row count after {keyword.value}")
        return int(token.text)

    def _parse_search_method(self) -> SearchMethod | None:
        if not self._match(TokenType.USING):
            return None
        token = self._expect(TokenType.IDENTIFIER, "an access method after USING")
        name = token.text.upper()
        method: SearchMethod | None = INDEX_METHODS.get(name)
        if method is None:
            method = RANKING_METHODS.get(name)
        if method is None:
            raise self._error_at(token, f"unknown access method '{token.text}'")
        return method

    def _parse_with_options(self) -> Mapping[str, OptionValue]:
        if not self._match(TokenType.WITH):
            return EMPTY_OPTIONS
        parenthesized = self._match(TokenType.LPAREN)
        options: dict[str, OptionValue] = {}
        self._parse_option_into(options)
        while self._match(TokenType.COMMA):
            self._parse_option_into(options)
        if parenthesized:
            self._expect(TokenType.RPAREN)
        return MappingProxyType(options)

    def _parse_option_into(self, options: dict[str, OptionValue]) -> None:
        token = self._expect_name_token("an option name")
        key = token.text.lower()
        if key in options:
            raise self._error_at(token, f"duplicate option '{token.text}'")
        self._expect(TokenType.EQUAL)
        options[key] = self._parse_option_value()

    def _parse_option_value(self) -> OptionValue:
        negative = self._match(TokenType.MINUS)
        token = self._advance()
        value = self._option_value_of(token)
        if not negative:
            return value
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise self._error_at(token, "expected a number after '-'")
        return -value

    def _option_value_of(self, token: Token) -> OptionValue:
        if token.type is TokenType.INTEGER:
            return int(token.text)
        if token.type is TokenType.FLOAT:
            return float(token.text)
        if token.type in STRING_TOKENS or token.type is TokenType.IDENTIFIER:
            return token.text
        if token.type is TokenType.TRUE:
            return True
        if token.type is TokenType.FALSE:
            return False
        if token.type is TokenType.NULL:
            return None
        raise self._error_at(token, "expected an option value")

    # --- INSERT / UPDATE / DELETE ------------------------------------------

    def _parse_insert(self) -> InsertStatement:
        self._expect(TokenType.INSERT)
        self._expect(TokenType.INTO)
        table = self._expect_name("a table name")
        columns = self._parse_optional_column_list()
        self._expect(TokenType.VALUES)
        first = self._parse_value_row(len(columns) if columns is not None else None)
        rows = [first]
        while self._match(TokenType.COMMA):
            rows.append(self._parse_value_row(len(first)))
        return InsertStatement(table=table, rows=tuple(rows), columns=columns)

    def _parse_optional_column_list(self) -> tuple[str, ...] | None:
        if not self._match(TokenType.LPAREN):
            return None
        columns = self._parse_unique_name_tokens("a column name")
        self._expect(TokenType.RPAREN)
        return tuple(token.text for token in columns)

    def _parse_value_row(self, arity: int | None) -> tuple[Expression, ...]:
        open_paren = self._expect(TokenType.LPAREN)
        values = [self._parse_expression()]
        while self._match(TokenType.COMMA):
            values.append(self._parse_expression())
        self._expect(TokenType.RPAREN)
        if arity is not None and len(values) != arity:
            raise self._error_at(
                open_paren, f"expected {arity} values per row, got {len(values)}"
            )
        return tuple(values)

    def _parse_update(self) -> UpdateStatement:
        self._expect(TokenType.UPDATE)
        table = self._expect_name("a table name")
        self._expect(TokenType.SET)
        assigned: set[str] = set()
        assignments = [self._parse_assignment(assigned)]
        while self._match(TokenType.COMMA):
            assignments.append(self._parse_assignment(assigned))
        return UpdateStatement(
            table=table,
            assignments=tuple(assignments),
            where=self._parse_optional_predicate(TokenType.WHERE),
        )

    def _parse_assignment(self, assigned: set[str]) -> Assignment:
        token = self._expect_name_token("a column name")
        if token.text.lower() in assigned:
            raise self._error_at(token, f"column '{token.text}' is assigned twice")
        assigned.add(token.text.lower())
        self._expect(TokenType.EQUAL)
        return Assignment(column=token.text, value=self._parse_expression())

    def _parse_delete(self) -> DeleteStatement:
        self._expect(TokenType.DELETE)
        self._expect(TokenType.FROM)
        table = self._expect_name("a table name")
        return DeleteStatement(table=table, where=self._parse_optional_predicate(TokenType.WHERE))

    # --- CREATE / DROP ------------------------------------------------------

    def _parse_create(self) -> Statement:
        self._expect(TokenType.CREATE)
        if self._match(TokenType.TABLE):
            return self._parse_create_table()
        if self._match(TokenType.INDEX):
            return self._parse_create_index()
        raise self._error_here("expected TABLE or INDEX after CREATE")

    def _parse_create_table(self) -> Statement:
        if_not_exists = self._parse_if_not_exists()
        name = self._expect_name("a table name")
        if self._match(TokenType.FROM):
            return self._parse_create_table_from_file(name, if_not_exists)
        columns = self._parse_column_definitions()
        return CreateTableStatement(name=name, columns=columns, if_not_exists=if_not_exists)

    def _parse_create_table_from_file(self, name: str, if_not_exists: bool) -> Statement:
        self._expect(TokenType.FILE)
        path = self._expect_string("a file path")
        index = None
        if self._match(TokenType.USING):
            self._expect(TokenType.INDEX)
            index = self._parse_index_spec()
        return CreateTableFromFileStatement(
            name=name, path=path, index=index, if_not_exists=if_not_exists
        )

    def _parse_column_definitions(self) -> tuple[ColumnDefinition, ...]:
        self._expect(TokenType.LPAREN)
        columns: list[ColumnDefinition] = []
        seen: set[str] = set()
        primary_key_columns: tuple[Token, ...] = ()
        while True:
            if self._check(TokenType.PRIMARY):
                if primary_key_columns:
                    raise self._error_here("table already declares a PRIMARY KEY")
                primary_key_columns = self._parse_table_primary_key()
            else:
                columns.append(self._parse_unique_column_definition(seen))
            if not self._match(TokenType.COMMA):
                break
        self._expect(TokenType.RPAREN)
        return self._apply_primary_key(tuple(columns), primary_key_columns)

    def _parse_unique_column_definition(self, seen: set[str]) -> ColumnDefinition:
        token = self._peek()
        column = self._parse_column_definition()
        key = column.name.lower()
        if key in seen:
            raise self._error_at(token, f"duplicate column '{column.name}'")
        seen.add(key)
        return column

    def _parse_table_primary_key(self) -> tuple[Token, ...]:
        self._expect(TokenType.PRIMARY)
        self._expect(TokenType.KEY)
        self._expect(TokenType.LPAREN)
        names = self._parse_unique_name_tokens("a column name")
        self._expect(TokenType.RPAREN)
        return names

    def _apply_primary_key(
        self, columns: tuple[ColumnDefinition, ...], key_tokens: tuple[Token, ...]
    ) -> tuple[ColumnDefinition, ...]:
        if not key_tokens:
            return columns
        known = {column.name.lower() for column in columns}
        for token in key_tokens:
            if token.text.lower() not in known:
                raise self._error_at(token, f"unknown column '{token.text}' in PRIMARY KEY")
        key_names = {token.text.lower() for token in key_tokens}
        return tuple(
            (
                column
                if column.name.lower() not in key_names
                else ColumnDefinition(
                    name=column.name,
                    data_type=column.data_type,
                    primary_key=True,
                    nullable=False,
                    unique=column.unique,
                    index=column.index,
                )
            )
            for column in columns
        )

    def _parse_column_definition(self) -> ColumnDefinition:
        name = self._expect_name("a column name")
        data_type = self._parse_data_type()
        primary_key = False
        nullable = True
        unique = False
        index: IndexType | None = None
        while True:
            if self._match(TokenType.PRIMARY):
                self._expect(TokenType.KEY)
                primary_key = True
                nullable = False
            elif self._match(TokenType.NOT):
                self._expect(TokenType.NULL)
                nullable = False
            elif self._match(TokenType.NULL):
                nullable = True
            elif self._match(TokenType.UNIQUE):
                unique = True
            elif self._match(TokenType.INDEX):
                index = self._parse_index_method()
            else:
                break
        return ColumnDefinition(
            name=name,
            data_type=data_type,
            primary_key=primary_key,
            nullable=nullable,
            unique=unique,
            index=index,
        )

    def _parse_data_type(self) -> DataType:
        token = self._expect(TokenType.IDENTIFIER, "a column type")
        kind = DATA_TYPES.get(token.text.upper())
        if kind is None:
            raise self._error_at(token, f"unknown column type '{token.text}'")
        size = self._parse_optional_size()
        if size is None and kind in TYPES_WITH_REQUIRED_SIZE:
            raise self._error_at(token, f"type {kind.value} requires a size")
        if size is not None and kind not in TYPES_WITH_OPTIONAL_SIZE | TYPES_WITH_REQUIRED_SIZE:
            raise self._error_at(token, f"type {kind.value} does not take a size")
        return DataType(kind=kind, size=size)

    def _parse_optional_size(self) -> int | None:
        if not self._match(TokenType.LPAREN):
            return None
        token = self._expect(TokenType.INTEGER, "a positive size")
        size = int(token.text)
        if size <= 0:
            raise self._error_at(token, "size must be greater than zero")
        self._expect(TokenType.RPAREN)
        return size

    def _parse_create_index(self) -> CreateIndexStatement:
        if_not_exists = self._parse_if_not_exists()
        name = self._advance().text if self._check_any(NAME_TOKENS) else None
        self._expect(TokenType.ON)
        table = self._expect_name("a table name")
        self._expect(TokenType.USING)
        return CreateIndexStatement(
            table=table, spec=self._parse_index_spec(), name=name, if_not_exists=if_not_exists
        )

    def _parse_index_spec(self) -> IndexSpec:
        method = self._parse_index_method()
        self._expect(TokenType.LPAREN)
        columns = self._parse_unique_name_tokens("a column name")
        self._expect(TokenType.RPAREN)
        return IndexSpec(
            method=method,
            columns=tuple(token.text for token in columns),
            options=self._parse_with_options(),
        )

    def _parse_index_method(self) -> IndexType:
        token = self._expect(TokenType.IDENTIFIER, "an index method")
        method = INDEX_METHODS.get(token.text.upper())
        if method is None:
            raise self._error_at(token, f"unknown index method '{token.text}'")
        return method

    def _parse_if_not_exists(self) -> bool:
        if not self._match(TokenType.IF):
            return False
        self._expect(TokenType.NOT)
        self._expect(TokenType.EXISTS)
        return True

    def _parse_if_exists(self) -> bool:
        if not self._match(TokenType.IF):
            return False
        self._expect(TokenType.EXISTS)
        return True

    def _parse_drop(self) -> Statement:
        self._expect(TokenType.DROP)
        if self._match(TokenType.TABLE):
            if_exists = self._parse_if_exists()
            return DropTableStatement(name=self._expect_name("a table name"), if_exists=if_exists)
        if self._match(TokenType.INDEX):
            if_exists = self._parse_if_exists()
            name = self._expect_name("an index name")
            table = self._expect_name("a table name") if self._match(TokenType.ON) else None
            return DropIndexStatement(name=name, table=table, if_exists=if_exists)
        raise self._error_here("expected TABLE or INDEX after DROP")

    # --- transactions -------------------------------------------------------

    def _parse_transaction(self) -> Statement:
        token = self._advance()
        self._match(TokenType.TRANSACTION)
        if token.type is TokenType.BEGIN:
            return BeginTransactionStatement()
        if token.type is TokenType.ROLLBACK:
            return RollbackTransactionStatement()
        return CommitTransactionStatement()

    # --- expressions --------------------------------------------------------

    @contextmanager
    def _nesting(self) -> Iterator[None]:
        if self._depth >= MAX_EXPRESSION_DEPTH:
            raise self._error_here(f"expression nests deeper than {MAX_EXPRESSION_DEPTH} levels")
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1

    def _parse_expression(self) -> Expression:
        with self._nesting():
            return self._parse_disjunction()

    def _parse_disjunction(self) -> Expression:
        left = self._parse_conjunction()
        while self._match(TokenType.OR):
            left = BinaryOperation(BinaryOperator.OR, left, self._parse_conjunction())
        return left

    def _parse_conjunction(self) -> Expression:
        left = self._parse_negation()
        while self._match(TokenType.AND):
            left = BinaryOperation(BinaryOperator.AND, left, self._parse_negation())
        return left

    def _parse_negation(self) -> Expression:
        if not self._match(TokenType.NOT):
            return self._parse_comparison()
        with self._nesting():
            return UnaryOperation(UnaryOperator.NOT, self._parse_negation())

    def _parse_comparison(self) -> Expression:
        left = self._parse_additive()
        while True:
            operator = COMPARISON_OPERATORS.get(self._peek().type)
            if operator is not None:
                self._advance()
                left = BinaryOperation(operator, left, self._parse_additive())
                continue
            predicate = self._parse_optional_pattern_predicate(left)
            if predicate is None:
                return left
            left = predicate

    def _parse_optional_pattern_predicate(self, operand: Expression) -> Expression | None:
        if self._check(TokenType.IS):
            return self._parse_null_predicate(operand)
        negated = self._check(TokenType.NOT)
        keyword_offset = 1 if negated else 0
        keyword = self._peek(keyword_offset).type
        if keyword not in (TokenType.BETWEEN, TokenType.IN, TokenType.LIKE):
            return None
        if negated:
            self._advance()
        self._advance()
        if keyword is TokenType.BETWEEN:
            return self._parse_between_predicate(operand, negated)
        if keyword is TokenType.IN:
            return self._parse_in_predicate(operand, negated)
        return LikePredicate(operand=operand, pattern=self._parse_additive(), negated=negated)

    def _parse_between_predicate(self, operand: Expression, negated: bool) -> BetweenPredicate:
        lower = self._parse_additive()
        self._expect(TokenType.AND)
        return BetweenPredicate(
            operand=operand, lower=lower, upper=self._parse_additive(), negated=negated
        )

    def _parse_in_predicate(self, operand: Expression, negated: bool) -> InPredicate:
        self._expect(TokenType.LPAREN)
        values = [self._parse_expression()]
        while self._match(TokenType.COMMA):
            values.append(self._parse_expression())
        self._expect(TokenType.RPAREN)
        return InPredicate(operand=operand, values=tuple(values), negated=negated)

    def _parse_null_predicate(self, operand: Expression) -> NullPredicate:
        self._expect(TokenType.IS)
        negated = self._match(TokenType.NOT)
        self._expect(TokenType.NULL)
        return NullPredicate(operand=operand, negated=negated)

    def _parse_additive(self) -> Expression:
        left = self._parse_multiplicative()
        while True:
            operator = ADDITIVE_OPERATORS.get(self._peek().type)
            if operator is None:
                return left
            self._advance()
            left = BinaryOperation(operator, left, self._parse_multiplicative())

    def _parse_multiplicative(self) -> Expression:
        left = self._parse_unary()
        while True:
            operator = MULTIPLICATIVE_OPERATORS.get(self._peek().type)
            if operator is None:
                return left
            self._advance()
            left = BinaryOperation(operator, left, self._parse_unary())

    def _parse_unary(self) -> Expression:
        if self._match(TokenType.MINUS):
            with self._nesting():
                return UnaryOperation(UnaryOperator.NEGATE, self._parse_unary())
        self._match(TokenType.PLUS)
        return self._parse_primary()

    def _parse_primary(self) -> Expression:
        token = self._advance()
        literal = self._literal_of(token)
        if literal is not None:
            return literal
        if token.type is TokenType.LPAREN:
            return self._parse_group_or_tuple()
        if token.type is TokenType.QUOTED_NAME:
            return self._parse_reference(token)
        if token.type is TokenType.IDENTIFIER:
            if self._check(TokenType.LPAREN):
                return self._parse_function_call(token)
            return self._parse_reference(token)
        raise self._error_at(token, "expected an expression")

    def _parse_group_or_tuple(self) -> Expression:
        first = self._parse_expression()
        if not self._check(TokenType.COMMA):
            self._expect(TokenType.RPAREN)
            return first
        elements = [first]
        while self._match(TokenType.COMMA):
            elements.append(self._parse_expression())
        self._expect(TokenType.RPAREN)
        return TupleExpression(elements=tuple(elements))

    def _literal_of(self, token: Token) -> Literal | None:
        if token.type is TokenType.INTEGER:
            return Literal(int(token.text))
        if token.type is TokenType.FLOAT:
            return Literal(float(token.text))
        if token.type is TokenType.STRING:
            return Literal(token.text)
        if token.type is TokenType.TRUE:
            return Literal(True)
        if token.type is TokenType.FALSE:
            return Literal(False)
        if token.type is TokenType.NULL:
            return Literal(None)
        return None

    def _parse_reference(self, token: Token) -> ColumnRef:
        if not self._match(TokenType.DOT):
            return ColumnRef(name=token.text)
        return ColumnRef(name=self._expect_name("a column name"), qualifier=token.text)

    def _parse_function_call(self, name_token: Token) -> FunctionCall:
        self._expect(TokenType.LPAREN)
        arguments: list[Expression] = []
        keyword_arguments: dict[str, Expression] = {}
        if not self._check(TokenType.RPAREN):
            self._parse_argument_into(name_token, arguments, keyword_arguments)
            while self._match(TokenType.COMMA):
                self._parse_argument_into(name_token, arguments, keyword_arguments)
        self._expect(TokenType.RPAREN)
        return FunctionCall(
            name=name_token.text,
            arguments=tuple(arguments),
            keyword_arguments=MappingProxyType(keyword_arguments),
        )

    def _parse_argument_into(
        self,
        name_token: Token,
        arguments: list[Expression],
        keyword_arguments: dict[str, Expression],
    ) -> None:
        if self._check(TokenType.IDENTIFIER) and self._check(TokenType.EQUAL, offset=1):
            token = self._advance()
            self._advance()
            key = token.text.lower()
            if key in keyword_arguments:
                raise self._error_at(token, f"duplicate argument '{token.text}'")
            keyword_arguments[key] = self._parse_expression()
            return
        if keyword_arguments:
            raise self._error_here(
                f"positional argument follows keyword argument in {name_token.text}()"
            )
        if self._check(TokenType.STAR) and self._peek(1).type is TokenType.RPAREN:
            self._advance()
            arguments.append(Star())
            return
        arguments.append(self._parse_expression())

    # --- token helpers ------------------------------------------------------

    def _peek(self, offset: int = 0) -> Token:
        index = min(self._index + offset, len(self._tokens) - 1)
        return self._tokens[index]

    def _check(self, token_type: TokenType, offset: int = 0) -> bool:
        return self._peek(offset).type is token_type

    def _check_any(self, token_types: frozenset[TokenType]) -> bool:
        return self._peek().type in token_types

    def _advance(self) -> Token:
        token = self._peek()
        if token.type is not TokenType.EOF:
            self._index += 1
        return token

    def _match(self, token_type: TokenType) -> bool:
        if not self._check(token_type):
            return False
        self._advance()
        return True

    def _expect(self, token_type: TokenType, description: str | None = None) -> Token:
        if not self._check(token_type):
            raise self._error_here(f"expected {description or token_type.value}")
        return self._advance()

    def _parse_unique_name_tokens(self, description: str) -> tuple[Token, ...]:
        tokens: list[Token] = []
        seen: set[str] = set()
        while True:
            token = self._expect_name_token(description)
            if token.text.lower() in seen:
                raise self._error_at(token, f"duplicate column '{token.text}'")
            seen.add(token.text.lower())
            tokens.append(token)
            if not self._match(TokenType.COMMA):
                return tuple(tokens)

    def _expect_name_token(self, description: str) -> Token:
        if not self._check_any(NAME_TOKENS):
            raise self._error_here(f"expected {description}")
        return self._advance()

    def _expect_name(self, description: str) -> str:
        return self._expect_name_token(description).text

    def _expect_string(self, description: str) -> str:
        if not self._check_any(STRING_TOKENS):
            raise self._error_here(f"expected {description}")
        return self._advance().text

    def _error_here(self, message: str) -> SqlSyntaxError:
        return self._error_at(self._peek(), message)

    def _error_at(self, token: Token, message: str) -> SqlSyntaxError:
        found = "end of input" if token.type is TokenType.EOF else f"'{token.text}'"
        return SqlSyntaxError(
            f"{message}, found {found}", token.line, token.column, self._line_text(token.line)
        )

    def _line_text(self, line: int) -> str:
        lines = self._source.splitlines()
        return lines[line - 1] if 0 < line <= len(lines) else ""


def parse(source: str) -> Statement:
    """Parses exactly one SQL statement and returns its AST root."""
    return Parser(source).parse_single()


def parse_script(source: str) -> list[Statement]:
    """Parses a `;`-separated script and returns one AST per statement."""
    return Parser(source).parse_script()
