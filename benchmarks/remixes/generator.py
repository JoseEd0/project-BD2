"""Remixes con la verdad conocida: fragmentos de pistas pegados con fundidos.

Un remix se planifica primero —qué canciones, qué tramo de cada una y dónde cae en la
subida— y se arma después con el audio. El plan sale solo de la semilla, así que la verdad
se puede comprobar sin audio y la misma semilla da los mismos remixes.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np


class RemixError(ValueError):
    """La forma pedida no deja armar un remix."""


@dataclass(frozen=True, slots=True)
class Span:
    """Un tramo de audio en segundos, `[start, end)`."""

    start: float
    end: float

    @property
    def seconds(self) -> float:
        return self.end - self.start

    def scaled(self, factor: float) -> Span:
        """El mismo tramo en un audio cuya duración se multiplicó por `factor`."""
        return Span(self.start * factor, self.end * factor)


@dataclass(frozen=True, slots=True)
class Fragment:
    """Un tramo `original` de la canción `song_id`, que suena en `upload` dentro del remix."""

    song_id: int
    original: Span
    upload: Span


@dataclass(frozen=True, slots=True)
class RemixPlan:
    """Los fragmentos de un remix, en orden, y si sus canciones están en el catálogo."""

    fragments: tuple[Fragment, ...]
    catalogued: bool

    @property
    def seconds(self) -> float:
        return self.fragments[-1].upload.end


@dataclass(frozen=True, slots=True)
class RemixShape:
    """Cuántos fragmentos lleva un remix, cuánto dura cada uno, cuánto se funden dos
    seguidos y cuánto dura cada pista de la que se cortan.

    Raises:
        RemixError: si un fragmento no cabe en una pista, el fundido no cabe en el
            fragmento más corto o los rangos están al revés.
    """

    min_fragments: int
    max_fragments: int
    min_fragment_seconds: float
    max_fragment_seconds: float
    crossfade_seconds: float
    source_seconds: float

    def __post_init__(self) -> None:
        if not 1 <= self.min_fragments <= self.max_fragments:
            raise RemixError("hace falta al menos un fragmento y el mínimo no supera al máximo")
        if not 0 < self.min_fragment_seconds <= self.max_fragment_seconds <= self.source_seconds:
            raise RemixError("cada fragmento tiene que caber en una pista")
        if not 0 <= 2 * self.crossfade_seconds < self.min_fragment_seconds:
            raise RemixError("los dos fundidos de un fragmento tienen que caber en él")


def plan_remix(
    rng: random.Random, songs: Sequence[int], shape: RemixShape, catalogued: bool
) -> RemixPlan:
    """Un remix de canciones distintas de `songs`, elegidas con `rng`. Cada fragmento empieza
    donde se funde con el anterior, así que el remix no tiene silencios.

    Raises:
        RemixError: si `songs` no tiene canciones distintas para todos los fragmentos.
    """
    count = rng.randint(shape.min_fragments, shape.max_fragments)
    if len(set(songs)) < count:
        raise RemixError(f"hacen falta {count} canciones distintas; hay {len(set(songs))}")
    chosen = rng.sample(sorted(set(songs)), count)
    fragments = []
    position = 0.0
    for song_id in chosen:
        seconds = rng.uniform(shape.min_fragment_seconds, shape.max_fragment_seconds)
        start = rng.uniform(0.0, shape.source_seconds - seconds)
        fragments.append(
            Fragment(song_id, Span(start, start + seconds), Span(position, position + seconds))
        )
        position += seconds - shape.crossfade_seconds
    return RemixPlan(tuple(fragments), catalogued)


def render(
    plan: RemixPlan,
    audio_of: Callable[[int], np.ndarray],
    sample_rate: int,
    crossfade_seconds: float,
) -> np.ndarray:
    """El remix en muestras mono de `float32`: cada fragmento cortado del audio de su canción
    (`audio_of`, mono a `sample_rate`) y sumado en su sitio, con un fundido lineal de
    `crossfade_seconds` donde se solapa con el anterior o el siguiente."""
    remix = np.zeros(frames(plan.seconds, sample_rate), dtype=np.float32)
    fade = frames(crossfade_seconds, sample_rate)
    last = len(plan.fragments) - 1
    for position, fragment in enumerate(plan.fragments):
        first = frames(fragment.upload.start, sample_rate)
        piece = _cut(audio_of(fragment.song_id), fragment, sample_rate)[: len(remix) - first]
        if position > 0:
            piece[:fade] *= _ramp(fade)
        if position < last:
            piece[len(piece) - fade :] *= _ramp(fade)[::-1]
        remix[first : first + len(piece)] += piece
    return remix


def frames(seconds: float, sample_rate: int) -> int:
    return round(seconds * sample_rate)


def _cut(audio: np.ndarray, fragment: Fragment, sample_rate: int) -> np.ndarray:
    first = frames(fragment.original.start, sample_rate)
    wanted = frames(fragment.upload.seconds, sample_rate)
    piece = np.array(audio[first : first + wanted], dtype=np.float32)
    if len(piece) < wanted:
        raise RemixError(
            f"la canción {fragment.song_id} dura menos que el tramo pedido "
            f"({fragment.original.end:.2f} s)"
        )
    return piece


def _ramp(length: int) -> np.ndarray:
    return np.linspace(0.0, 1.0, length, endpoint=False, dtype=np.float32)
