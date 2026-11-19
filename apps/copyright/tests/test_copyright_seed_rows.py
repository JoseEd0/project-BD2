"""Las filas que el trabajo de carga manda al gestor: canciones y tramos con sus ids."""

from __future__ import annotations

import pytest

from songcheck.catalog import CatalogSong
from songcheck.errors import SeedError
from songcheck.seed import SEGMENT_ID_STRIDE, segment_count, segment_id, segment_rows, song_rows


def song(track_id: int, duration: float) -> CatalogSong:
    return CatalogSong(track_id, "t", "a", "Rock", duration, f"pistas/{track_id}.mp3", 1, 0)


def test_segment_rows_carry_the_song_bounds_and_the_fragment_path():
    rows = list(segment_rows([song(2, 10.0), song(140, 4.0)], "/gestor/fma_small"))
    assert [row["id"] for row in rows] == [2000, 2001, 2002, 140000]
    assert rows[1] == {
        "id": 2001,
        "cancion": 2,
        "inicio": 2.5,
        "fin": 7.5,
        "ruta": "/gestor/fma_small/pistas/2.mp3#t=2.5,7.5",
    }
    assert segment_count([song(2, 10.0), song(140, 4.0)]) == len(rows)


def test_a_song_with_too_many_windows_cannot_get_ids():
    assert segment_id(7, SEGMENT_ID_STRIDE - 1) == 7 * SEGMENT_ID_STRIDE + SEGMENT_ID_STRIDE - 1
    with pytest.raises(SeedError, match="demasiado larga"):
        segment_id(7, SEGMENT_ID_STRIDE)


def test_song_rows_carry_the_engine_path_and_the_search_text():
    (row,) = song_rows([song(2, 29.9)], "/gestor/fma_small")
    assert row["ruta"] == "/gestor/fma_small/pistas/2.mp3"
    assert row["busqueda"] == "t a"
    assert (row["id"], row["genero"], row["duracion"]) == (2, "Rock", 29.9)
