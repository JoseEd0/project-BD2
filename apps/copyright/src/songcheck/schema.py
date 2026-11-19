"""Las tablas del servicio en el gestor, con el prefijo `cr_`.

- `cr_canciones`: una fila por canción original, con su texto de búsqueda (título y artista)
  en un índice invertido.
- `cr_segmentos`: una fila por ventana de cada canción, cuya ruta es el tramo
  (`pista.mp3#t=2.5,7.5`), con un HNSW sobre los MFCC en bolsa de palabras de la Parte 4.

`SCHEMA` son las migraciones repetibles que aplica el API al arrancar. El HNSW no está ahí:
el gestor entrena su vocabulario con las filas de la tabla y no lo crea sobre una vacía, así
que lo crea el trabajo de carga, después de cargar los tramos (`SEGMENT_INDEX`).
"""

from __future__ import annotations

SONGS_TABLE = "cr_canciones"
SEGMENTS_TABLE = "cr_segmentos"
SEGMENT_INDEX_NAME = "cr_segmentos_ruta"
SEGMENT_SONG_COLUMN = "cancion"
SEGMENT_START_COLUMN = "inicio"
SEGMENT_END_COLUMN = "fin"
GENRE_LENGTH = 40
# Una ruta absoluta del gestor más `#t=inicio,fin`: la de Docker mide unos 70 caracteres y
# la de un checkout local unos 110.
PATH_LENGTH = 255
# Los títulos y artistas de FMA están casi todos en inglés: con el analizador inglés,
# «Dancing» y «dance» comparten raíz.
SEARCH_LANGUAGE = "en"

SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS {SONGS_TABLE} ("
    " id INT PRIMARY KEY, titulo TEXT, artista TEXT,"
    f" genero VARCHAR({GENRE_LENGTH}), duracion FLOAT, ruta VARCHAR({PATH_LENGTH}),"
    " busqueda TEXT)",
    f"CREATE INDEX IF NOT EXISTS cr_canciones_busqueda ON {SONGS_TABLE} USING INVERTED (busqueda)"
    f" WITH (idioma = '{SEARCH_LANGUAGE}')",
    f"CREATE TABLE IF NOT EXISTS {SEGMENTS_TABLE} ("
    f" id INT PRIMARY KEY, {SEGMENT_SONG_COLUMN} INT, {SEGMENT_START_COLUMN} FLOAT,"
    f" {SEGMENT_END_COLUMN} FLOAT, ruta VARCHAR({PATH_LENGTH}))",
)
SEGMENT_INDEX = (
    f"CREATE INDEX IF NOT EXISTS {SEGMENT_INDEX_NAME} ON {SEGMENTS_TABLE} USING HNSW (ruta)"
    " WITH (metric = cosine)"
)
