"""Deshacer cambios sobre columnas TEXT.

El registro de deshacer guarda filas lógicas, con el texto entero. Un borrado libera el
valor en el acto y deshacerlo lo vuelve a guardar; al terminar, el archivo de valores tiene
lo mismo que antes de la transacción, sin huérfanos.
"""

import pytest

from query.engine import Engine
from query.table import DuplicatePrimaryKeyError
from txn import Session

LONG_TEXT = "texto largo " * 1_500
ORGANIZATIONS = [
    "id INT PRIMARY KEY",
    "id INT PRIMARY KEY INDEX SEQ",
    "id INT PRIMARY KEY INDEX BTREE",
]
ROWS = ((1, LONG_TEXT), (2, "corto"), (3, None))


def stored_values(engine: Engine) -> int:
    return int(engine.table("notas").describe_structure()["text_values"]["values"])


@pytest.fixture(params=ORGANIZATIONS)
def notas(session: Session, request: pytest.FixtureRequest) -> Session:
    session.execute(f"CREATE TABLE notas ({request.param}, cuerpo TEXT)")
    session.execute(f"INSERT INTO notas VALUES (1, '{LONG_TEXT}'), (2, 'corto'), (3, NULL)")
    return session


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO notas VALUES (4, 'nueva'), (5, NULL)",
        "DELETE FROM notas WHERE id < 3",
        "UPDATE notas SET cuerpo = 'cambiado' WHERE id = 1",
        "UPDATE notas SET cuerpo = NULL WHERE id = 2",
        "UPDATE notas SET cuerpo = 'ahora sí' WHERE id = 3",
        "UPDATE notas SET id = id + 10",
    ],
)
def test_rolling_back_restores_the_texts_and_leaves_no_orphans(
    notas: Session, engine: Engine, statement: str
):
    notas.execute("BEGIN TRANSACTION")
    notas.execute(statement)
    notas.execute("ROLLBACK")
    assert notas.execute("SELECT * FROM notas ORDER BY id").rows == ROWS
    assert stored_values(engine) == 2


def test_committing_keeps_the_new_texts(notas: Session, engine: Engine):
    notas.execute("BEGIN TRANSACTION")
    notas.execute("UPDATE notas SET cuerpo = 'nuevo' WHERE id = 1")
    notas.execute("DELETE FROM notas WHERE id = 2")
    notas.execute("COMMIT")
    assert notas.execute("SELECT * FROM notas ORDER BY id").rows == ((1, "nuevo"), (3, None))
    assert stored_values(engine) == 1


def test_a_failed_statement_inside_a_transaction_is_undone_alone(
    notas: Session, engine: Engine
):
    notas.execute("BEGIN TRANSACTION")
    notas.execute("DELETE FROM notas WHERE id = 1")
    with pytest.raises(DuplicatePrimaryKeyError):
        notas.execute("INSERT INTO notas VALUES (7, 'entra'), (2, 'repetida')")
    assert stored_values(engine) == 1
    notas.execute("ROLLBACK")
    assert notas.execute("SELECT * FROM notas ORDER BY id").rows == ROWS
    assert stored_values(engine) == 2
