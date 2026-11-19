"""El SQL de las páginas del catálogo. El API contra el gestor real está en
`tests/copyright/test_copyright_api_end_to_end.py`."""

from __future__ import annotations

from songcheck.api import songs_query


def test_the_song_page_query_ranks_text_and_filters_genre():
    assert songs_query(None, None, 20, 0) == (
        "SELECT id, titulo, artista, genero, duracion FROM cr_canciones ORDER BY id"
        " LIMIT $1 OFFSET $2",
        [20, 0],
    )
    sql, params = songs_query("love", "Rock", 5, 10)
    assert sql == (
        "SELECT id, titulo, artista, genero, duracion, SCORE() AS puntaje FROM cr_canciones"
        " WHERE MATCH(busqueda, $1) AND genero = $2 USING BM25 ORDER BY puntaje DESC"
        " LIMIT $3 OFFSET $4"
    )
    assert params == ["love", "Rock", 5, 10]
