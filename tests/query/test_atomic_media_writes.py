"""Una escritura sobre una columna de rutas con índice HNSW no deja nada si un archivo falla.

Es el caso de la issue #115: un `INSERT` en autocommit con una ruta que no existe devolvía
el error y dejaba la fila. Lo que se sabe sin decodificar —que el archivo exista y que el
tramo `#t=` empiece antes del final— se rechaza antes de escribir; lo que solo se sabe al
extraer, deshaciendo lo escrito.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.multimedia.media_support import chirp, write_audio

from config import EngineConfig
from index.similarity import media as media_index
from multimedia import FragmentOutOfRangeError
from query.engine import Engine
from query.loader import LoaderError
from txn import LockManager, Session, TransactionManager

CLIP_SECONDS = (1.0, 1.4, 1.8, 2.2)
VOCABULARY = 4
BEYOND_THE_END = "#t=50,60"


class InjectedError(Exception):
    """El fallo de extracción que se inyecta."""


@pytest.fixture
def media(tmp_path: Path) -> Path:
    directory = tmp_path / "medios"
    directory.mkdir()
    return directory


@pytest.fixture
def clips(media: Path) -> list[Path]:
    return [write_audio(media / f"c{i}.wav", chirp(seconds)) for i, seconds in enumerate(CLIP_SECONDS)]


@pytest.fixture
def config(tmp_path: Path, media: Path) -> EngineConfig:
    return EngineConfig(
        data_directory=tmp_path / "datos", import_directories=(media,), feature_workers=1
    )


@pytest.fixture
def engine(config: EngineConfig, clips: list[Path]) -> Iterator[Engine]:
    with Engine(config) as opened:
        opened.execute("CREATE TABLE c (id INT PRIMARY KEY, ruta VARCHAR(300))")
        values = ", ".join(f"({key}, '{path}')" for key, path in enumerate(clips[:3]))
        opened.execute(f"INSERT INTO c VALUES {values}")
        opened.execute(f"CREATE INDEX ic ON c USING HNSW (ruta) WITH (vocabulary = {VOCABULARY})")
        yield opened


@pytest.fixture
def session(engine: Engine, config: EngineConfig) -> Iterator[Session]:
    with Session(engine, LockManager(config), TransactionManager()) as opened:
        yield opened


def state(engine: Engine | Session, clips: list[Path]) -> tuple[object, ...]:
    rows = engine.execute("SELECT * FROM c ORDER BY id").rows
    similar = engine.execute(f"SELECT id FROM c WHERE SIMILAR_TO('{clips[0]}', k = 10)").rows
    by_key = [engine.execute(f"SELECT id FROM c WHERE id = {row[0]}").rows for row in rows]
    assert sorted(similar) == [(row[0],) for row in rows]
    assert by_key == [((row[0],),) for row in rows]
    return rows, sorted(similar)


def fail_extracting(monkeypatch: pytest.MonkeyPatch, victim: Path) -> None:
    original = media_index.extract_features

    def extract(path: Path, *arguments: object) -> object:
        if path == victim:
            raise InjectedError(f"no se pudo describir {path.name}")
        return original(path, *arguments)

    monkeypatch.setattr(media_index, "extract_features", extract)


def assert_failure_leaves_nothing(
    session: Session,
    clips: list[Path],
    statement: str,
    error: type[Exception],
    in_transaction: bool,
) -> None:
    if in_transaction:
        session.execute("BEGIN")
    before = state(session, clips)

    with pytest.raises(error):
        session.execute(statement)

    assert session.in_transaction is in_transaction
    assert state(session, clips) == before
    if in_transaction:
        session.execute(f"INSERT INTO c VALUES (20, '{clips[2]}')")
        session.execute("COMMIT")
        assert session.execute("SELECT id FROM c WHERE id = 20").rows == ((20,),)


@pytest.mark.parametrize("in_transaction", [False, True], ids=["autocommit", "transaction"])
@pytest.mark.parametrize(
    ("written", "error"),
    [("{media}/no-existe.wav", LoaderError), ("{clip}" + BEYOND_THE_END, FragmentOutOfRangeError)],
    ids=["missing-file", "fragment-beyond-the-end"],
)
def test_a_path_the_index_cannot_read_leaves_no_row(
    session: Session,
    clips: list[Path],
    media: Path,
    written: str,
    error: type[Exception],
    in_transaction: bool,
):
    bad = written.format(media=media, clip=clips[1])
    statement = f"INSERT INTO c VALUES (10, '{clips[3]}'), (11, '{bad}')"

    assert_failure_leaves_nothing(session, clips, statement, error, in_transaction)


@pytest.mark.parametrize("in_transaction", [False, True], ids=["autocommit", "transaction"])
def test_an_extraction_that_fails_after_writing_is_undone(
    session: Session,
    clips: list[Path],
    in_transaction: bool,
    monkeypatch: pytest.MonkeyPatch,
):
    fail_extracting(monkeypatch, clips[3])
    statement = f"INSERT INTO c VALUES (10, '{clips[1]}'), (11, '{clips[3]}')"

    assert_failure_leaves_nothing(session, clips, statement, InjectedError, in_transaction)


@pytest.mark.parametrize("in_transaction", [False, True], ids=["autocommit", "transaction"])
def test_an_update_whose_new_file_fails_keeps_the_old_paths(
    session: Session,
    clips: list[Path],
    in_transaction: bool,
    monkeypatch: pytest.MonkeyPatch,
):
    fail_extracting(monkeypatch, clips[3])
    statement = f"UPDATE c SET ruta = '{clips[3]}' WHERE id >= 1"

    assert_failure_leaves_nothing(session, clips, statement, InjectedError, in_transaction)


def test_an_unreadable_path_is_rejected_before_writing_even_without_a_session(
    engine: Engine, clips: list[Path], media: Path
):
    before = state(engine, clips)

    with pytest.raises(LoaderError):
        engine.execute(f"INSERT INTO c VALUES (10, '{clips[3]}'), (11, '{media}/no-existe.wav')")

    assert state(engine, clips) == before
