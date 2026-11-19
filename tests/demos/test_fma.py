"""La elección de pistas de la demo de audio. Leer `tracks.csv` y bajar las pistas del `.zip`
es del servicio de copyright, y se prueba con él (`apps/copyright/tests`)."""

from collections import Counter

from demos import descargar_fma as fma
from songcheck.fma.archive import AudioEntry
from songcheck.fma.tracks import Track

GENRES = ("Rock", "Hip-Hop", "Folk", "Electronic")
TRACKS_PER_GENRE = 10
SEED = 9
ENTRY_BYTES = 500_000


def tracks() -> list[Track]:
    return [
        Track(track_id=number, title=f"Tema {number}", artist=f"Artista {number % 7}", genre=genre)
        for number, genre in enumerate(
            (genre for genre in GENRES for _ in range(TRACKS_PER_GENRE)), start=1
        )
    ]


def test_a_track_becomes_an_item_with_its_genre_as_class_and_artist_as_group():
    item = fma.media_item(Track(155066, "Final", "Alguien", "Rock"))
    assert (item.path, item.title, item.category, item.group) == (
        "pistas/155/155066.mp3",
        "Final",
        "Rock",
        "Alguien",
    )


def test_the_chosen_tracks_are_balanced_by_genre_and_reproducible():
    items = [fma.media_item(track) for track in tracks()]
    chosen = fma.choose_tracks(items, 8, SEED)
    assert Counter(item.category for item in chosen) == dict.fromkeys(GENRES, 2)
    assert fma.choose_tracks(reversed(items), 8, SEED) == chosen


def test_the_chosen_tracks_are_fetched_by_their_zip_entries():
    index = {
        track.track_id: AudioEntry(track.track_id, f"fma_small/{track.track_id}", ENTRY_BYTES, 0)
        for track in tracks()
    }
    chosen = fma.choose_tracks([fma.media_item(track) for track in tracks()], 4, SEED)
    entries = fma.entries_of(chosen, index)
    assert [f"pistas/{entry.relative_path}" for entry in entries] == [item.path for item in chosen]
