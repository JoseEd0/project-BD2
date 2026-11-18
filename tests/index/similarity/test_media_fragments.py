"""Tramos de audio (`ruta#t=inicio,fin`) en una columna con índice de similitud y la
consulta por segmentos de un audio entero."""

from pathlib import Path

import numpy as np
import pytest
from tests.multimedia.media_support import (
    SAMPLE_RATE,
    TEXTURE_SIDE,
    chirp,
    textured_pixels,
    tone,
    write_audio,
    write_image,
)

import multimedia.parallel
import multimedia.segments
from config import EngineConfig
from errors import DomainError
from index.similarity import MediaSimilarityIndex
from multimedia import (
    FragmentOnImageError,
    FragmentOutOfRangeError,
    MalformedFragmentError,
    MediaReference,
    SegmentLimitError,
    decode_audio,
)
from query.engine import Engine

SONGS = 3
SONG_SECONDS = 4.0
WINDOW = 1.0
HOP = 0.5
VOCABULARY = 8
K = 3
FRAGMENT_STARTS = (0.0, 1.0, 2.0, 3.0)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    directory = tmp_path / "medios"
    directory.mkdir()
    for number in range(SONGS):
        samples = np.roll(chirp(SONG_SECONDS), number * SAMPLE_RATE // 3)
        write_audio(directory / f"cancion{number}.wav", samples + 0.1 * tone(SONG_SECONDS))
    write_image(directory / "foto.png", textured_pixels(TEXTURE_SIDE, TEXTURE_SIDE))
    return directory


@pytest.fixture
def config(tmp_path: Path, media: Path) -> EngineConfig:
    return EngineConfig(
        data_directory=tmp_path / "datos", import_directories=(media,), feature_workers=1
    )


@pytest.fixture
def decodes(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Los audios decodificados enteros, al construir o al consultar por segmentos."""
    decoded: list[Path] = []

    def counting(path: Path):
        decoded.append(path)
        return decode_audio(path)

    monkeypatch.setattr(multimedia.parallel, "decode_audio", counting)
    monkeypatch.setattr(multimedia.segments, "decode_audio", counting)
    return decoded


def fragment_rows(media: Path) -> list[tuple[int, str]]:
    """Los tramos de cada canción, intercalados: el orden de la tabla no los agrupa."""
    return [
        (number * len(FRAGMENT_STARTS) + position, f"{media / f'cancion{number}.wav'}#t={start:g},{start + WINDOW:g}")
        for position, start in enumerate(FRAGMENT_STARTS)
        for number in range(SONGS)
    ]


def fragment_table(config: EngineConfig, media: Path, method: str = "HNSW") -> Engine:
    engine = Engine(config)
    engine.execute("CREATE TABLE tramos (id INT PRIMARY KEY, ruta VARCHAR(250))")
    values = ", ".join(f"({key}, '{path}')" for key, path in fragment_rows(media))
    engine.execute(f"INSERT INTO tramos VALUES {values}")
    engine.execute(
        f"CREATE INDEX i ON tramos USING {method} (ruta) "
        f"WITH (METRIC = euclidean, vocabulary = {VOCABULARY})"
    )
    return engine


def search(engine: Engine, query: str, k: int = K) -> list[tuple[int, float]]:
    sql = f"SELECT id, SIMILARITY_SCORE() FROM tramos WHERE SIMILAR_TO('{query}', k = {k})"
    return [(row[0], row[1]) for row in engine.execute(sql).rows]


def media_index(engine: Engine) -> MediaSimilarityIndex:
    index = engine.table("tramos").similarity_index("i")
    assert isinstance(index, MediaSimilarityIndex)
    return index


def test_building_decodes_each_file_once(config, media, decodes, monkeypatch):
    ranged: list[Path] = []
    real = multimedia.parallel.extract_features

    def counting(path: Path, *arguments):
        ranged.append(path)
        return real(path, *arguments)

    monkeypatch.setattr(multimedia.parallel, "extract_features", counting)

    with fragment_table(config, media) as engine:
        assert media_index(engine).describe()["vectors"] == SONGS * len(FRAGMENT_STARTS)

    assert sorted(decodes) == sorted(media / f"cancion{number}.wav" for number in range(SONGS))
    assert ranged == []


def test_a_training_sample_takes_whole_files(config, media, decodes):
    """Con una muestra de dos filas, el vocabulario se entrena con los tramos de un archivo
    entero, y ningún archivo se decodifica dos veces."""
    sampled = EngineConfig(**{**config_fields(config), "vocabulary_training_objects": 2})

    with fragment_table(sampled, media):
        pass

    assert len(decodes) == SONGS
    assert len(set(decodes)) == SONGS


def config_fields(config: EngineConfig) -> dict:
    return {name: getattr(config, name) for name in config.__dataclass_fields__}


@pytest.mark.parametrize("method", ["HNSW", "IVF"])
def test_a_fragment_finds_itself(config, media, method):
    with fragment_table(config, media, method) as engine:
        for key, path in fragment_rows(media)[:: SONGS + 1]:
            assert search(engine, path, 1) == [(key, pytest.approx(1.0))]


def test_an_inserted_fragment_is_indexed(config, media):
    with fragment_table(config, media) as engine:
        path = f"{media / 'cancion0.wav'}#t=0.5,1.5"
        engine.execute(f"INSERT INTO tramos VALUES (100, '{path}')")

        assert search(engine, path, 1) == [(100, pytest.approx(1.0))]


def test_a_fragment_past_the_end_cannot_be_indexed(config, media):
    with fragment_table(config, media) as engine, pytest.raises(FragmentOutOfRangeError):
        engine.execute(f"INSERT INTO tramos VALUES (100, '{media / 'cancion0.wav'}#t=10,11')")


def test_a_malformed_fragment_in_the_query_is_an_error(config, media):
    with fragment_table(config, media) as engine, pytest.raises(MalformedFragmentError):
        search(engine, f"{media / 'cancion0.wav'}#t=uno,dos")


def test_a_photo_with_a_fragment_cannot_be_indexed(config, media):
    with Engine(config) as engine:
        engine.execute("CREATE TABLE fotos (id INT PRIMARY KEY, ruta VARCHAR(250))")
        engine.execute(f"INSERT INTO fotos VALUES (1, '{media / 'foto.png'}#t=0,1')")

        with pytest.raises(FragmentOnImageError):
            engine.execute("CREATE INDEX i ON fotos USING HNSW (ruta)")


@pytest.mark.parametrize("method", ["HNSW", "IVF"])
def test_every_segment_gets_what_its_fragment_gets_alone(config, media, decodes, method):
    song = media / "cancion1.wav"
    with fragment_table(config, media, method) as engine:
        index = media_index(engine)
        decodes.clear()

        result = index.similar_segments(song, WINDOW, HOP, K)

        assert decodes == [song]
        assert result.duration == pytest.approx(SONG_SECONDS)
        assert len(result.segments) == 7
        for segment in result.segments:
            alone = index.similar(MediaReference(song, segment.window), K).rows
            assert [row.record_id for row in segment.rows] == [row.record_id for row in alone]
            assert [row.score for row in segment.rows] == pytest.approx(
                [row.score for row in alone], rel=1e-6
            )


def test_a_segment_query_over_the_limits_is_refused(config, media):
    limited = EngineConfig(**{**config_fields(config), "max_segments": 2})
    with fragment_table(limited, media) as engine, pytest.raises(SegmentLimitError):
        media_index(engine).similar_segments(media / "cancion0.wav", WINDOW, HOP, K)


def test_a_photo_has_no_segments(config, media):
    with fragment_table(config, media) as engine, pytest.raises(DomainError):
        media_index(engine).similar_segments(media / "foto.png", WINDOW, HOP, K)
