"""La votación por desfase: qué canciones del catálogo suenan, alineadas, dentro de una subida.

Buscar cada ventana por separado da falsos positivos: dos canciones del mismo género se
parecen en un tramo suelto. Lo que delata una copia es que **varias ventanas seguidas** de la
subida tengan por vecina a la misma canción con el **mismo desfase** `t_subida - t_original`
(la idea de Shazam y de Content ID). Para cada canción candidata:

1. cada vecino de cada ventana vota por un desfase, medido en saltos de ventana: las ventanas
   de la subida y las del catálogo empiezan en múltiplos del mismo salto, así que el desfase
   de dos ventanas es un número entero de saltos;
2. el apoyo de un desfase son las ventanas con algún voto a un salto o menos de él: una copia
   que no empieza en la rejilla del catálogo reparte sus votos entre dos desfases vecinos;
   a igual apoyo gana el pico del histograma, la mayor similitud por ese desfase exacto;
3. el desfase con más apoyo da las rachas de ventanas consecutivas que lo apoyan; las que
   llegan a `min_run_windows` son coincidencias, y sus ventanas ya no votan por otro desfase
   de la misma canción; se repite con el siguiente desfase hasta que ninguno alcanza.

Puntaje = similitud media de la racha · cobertura, y la cobertura es la fracción de las
ventanas que las dos grabaciones podrían compartir: `ventanas de la racha / min(ventanas de
la subida, ventanas del original)`. Dividir solo por las de la subida castigaría un sample de
10 s dentro de una canción de 3 min, justo lo que se busca; dividir solo por las del original,
un fragmento corto de un original largo. Con el mínimo, una copia que cubre todo lo que
podía cubrir vale 1, sea la subida más larga o más corta que el original.

Todo es puro y determinista: el orden de las entradas no cambia la respuesta, y los empates
se rompen por similitud, desfase, id de canción y posición, en ese orden.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# Un voto cuenta para su desfase y para los dos vecinos: una copia desplazada media ventana
# respecto de la rejilla del catálogo cae entre dos tramos y vota por los dos.
OFFSET_TOLERANCE_HOPS = 1


class VotingError(ValueError):
    """Los parámetros de la votación no tienen sentido."""


@dataclass(frozen=True, slots=True)
class VotingParameters:
    """El salto de las ventanas (el mismo en la subida y en el catálogo) y la racha más corta
    que cuenta como coincidencia.

    Raises:
        VotingError: si el salto no es positivo o la racha mínima es menor que una ventana.
    """

    hop_seconds: float
    min_run_windows: int

    def __post_init__(self) -> None:
        if self.hop_seconds <= 0 or self.min_run_windows < 1:
            raise VotingError(
                f"el salto tiene que ser positivo y la racha de al menos una ventana; llegaron "
                f"{self.hop_seconds:g} s y {self.min_run_windows}"
            )


@dataclass(frozen=True, slots=True)
class Neighbour:
    """Un tramo del catálogo parecido a una ventana: su canción, dónde empieza y termina en el
    original y la similitud que dio el índice."""

    song_id: int
    start: float
    end: float
    similarity: float


@dataclass(frozen=True, slots=True)
class QueryWindow:
    """Una ventana de la subida, en segundos desde su inicio, y sus vecinos del catálogo."""

    start: float
    end: float
    neighbours: tuple[Neighbour, ...]


@dataclass(frozen=True, slots=True)
class Span:
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class Run:
    """Ventanas consecutivas de la subida alineadas con una canción por el mismo desfase.

    Attributes:
        song_id: la canción del catálogo.
        offset_seconds: `t_subida - t_original` del desfase con más apoyo.
        first_window, last_window: posiciones de la primera y la última ventana, incluidas.
        upload: el tramo de la subida que cubren.
        original: el tramo del original que cubren sus mejores vecinos.
        similarities: la similitud del mejor vecino alineado de cada ventana, en orden.
    """

    song_id: int
    offset_seconds: float
    first_window: int
    last_window: int
    upload: Span
    original: Span
    similarities: tuple[float, ...]

    @property
    def windows(self) -> int:
        return self.last_window - self.first_window + 1

    @property
    def mean_similarity(self) -> float:
        return sum(self.similarities) / len(self.similarities)


@dataclass(frozen=True, slots=True)
class Match:
    """Una racha con su puntaje y si pasa el umbral."""

    run: Run
    coverage: float
    score: float
    above_threshold: bool


@dataclass(frozen=True, slots=True)
class _Vote:
    window: int
    offset_hops: int
    neighbour: Neighbour


def aligned_runs(windows: Sequence[QueryWindow], parameters: VotingParameters) -> list[Run]:
    """Las rachas de cada canción que vota alguna ventana, ordenadas por canción y posición.

    Una subida con menos ventanas que `min_run_windows` pide rachas de todas sus ventanas.
    """
    min_run = min(parameters.min_run_windows, len(windows))
    runs: list[Run] = []
    for song_id, votes in sorted(_votes_by_song(windows, parameters.hop_seconds).items()):
        runs.extend(_song_runs(song_id, votes, windows, parameters.hop_seconds, min_run))
    return runs


def coverage(run_windows: int, upload_windows: int, original_windows: int) -> float:
    """La fracción de las ventanas que la subida y el original podrían compartir que cubre una
    racha de `run_windows`, a lo sumo 1."""
    shareable = min(upload_windows, original_windows)
    return min(1.0, run_windows / shareable) if shareable > 0 else 0.0


def score_runs(
    runs: Iterable[Run],
    upload_windows: int,
    original_windows: Mapping[int, int],
    threshold: float,
) -> list[Match]:
    """Las rachas con su puntaje, de mayor a menor; `original_windows` da las ventanas de cada
    canción en el catálogo.

    Raises:
        KeyError: si falta la canción de alguna racha en `original_windows`.
    """
    matches = []
    for run in runs:
        covered = coverage(run.windows, upload_windows, original_windows[run.song_id])
        score = run.mean_similarity * covered
        matches.append(Match(run, covered, score, score >= threshold))
    return sorted(matches, key=_match_order)


def _votes_by_song(windows: Sequence[QueryWindow], hop: float) -> dict[int, list[_Vote]]:
    votes: defaultdict[int, list[_Vote]] = defaultdict(list)
    for position, window in enumerate(windows):
        for neighbour in window.neighbours:
            offset_hops = round((window.start - neighbour.start) / hop)
            votes[neighbour.song_id].append(_Vote(position, offset_hops, neighbour))
    return votes


def _song_runs(
    song_id: int,
    votes: list[_Vote],
    windows: Sequence[QueryWindow],
    hop: float,
    min_run: int,
) -> list[Run]:
    runs: list[Run] = []
    remaining = votes
    tried: set[int] = set()
    while (offset := _best_offset(remaining, tried, min_run)) is not None:
        tried.add(offset)
        aligned = _best_aligned_votes(remaining, offset)
        found = [group for group in _consecutive_groups(sorted(aligned)) if len(group) >= min_run]
        runs.extend(_run_of(song_id, offset * hop, group, aligned, windows) for group in found)
        consumed = {position for group in found for position in group}
        remaining = [vote for vote in remaining if vote.window not in consumed]
    return runs


def _best_offset(votes: list[_Vote], tried: set[int], min_run: int) -> int | None:
    """El desfase no probado con más ventanas de apoyo; `None` si ninguno llega a `min_run`.

    A igual apoyo gana el pico del histograma: la mayor similitud de los votos exactamente
    por ese desfase. Una copia alineada con la rejilla trae también a los tramos solapados de
    antes y de después, que apoyan con la tolerancia a los desfases vecinos tanto como al
    suyo, pero con menos similitud. Al final, el desfase menor.
    """
    candidates = {vote.offset_hops for vote in votes} - tried
    if not candidates:
        return None
    best = max(candidates, key=lambda offset: _offset_rank(votes, offset))
    windows, *_ = _offset_rank(votes, best)
    return best if windows >= min_run else None


def _offset_rank(votes: list[_Vote], offset: int) -> tuple[int, float, int]:
    support = _best_aligned_votes(votes, offset, OFFSET_TOLERANCE_HOPS)
    exact = _best_aligned_votes(votes, offset, tolerance_hops=0)
    return len(support), sum(vote.neighbour.similarity for vote in exact.values()), -offset


def _best_aligned_votes(
    votes: list[_Vote], offset: int, tolerance_hops: int = OFFSET_TOLERANCE_HOPS
) -> dict[int, _Vote]:
    """El mejor voto a `tolerance_hops` saltos o menos de `offset` de cada ventana que tiene
    alguno."""
    best: dict[int, _Vote] = {}
    for vote in votes:
        if abs(vote.offset_hops - offset) > tolerance_hops:
            continue
        current = best.get(vote.window)
        if current is None or _vote_rank(vote, offset) < _vote_rank(current, offset):
            best[vote.window] = vote
    return best


def _vote_rank(vote: _Vote, offset: int) -> tuple[float, int, float]:
    return (-vote.neighbour.similarity, abs(vote.offset_hops - offset), vote.neighbour.start)


def _consecutive_groups(positions: list[int]) -> list[list[int]]:
    groups: list[list[int]] = []
    for position in positions:
        if groups and groups[-1][-1] == position - 1:
            groups[-1].append(position)
        else:
            groups.append([position])
    return groups


def _run_of(
    song_id: int,
    offset_seconds: float,
    group: list[int],
    aligned: dict[int, _Vote],
    windows: Sequence[QueryWindow],
) -> Run:
    chosen = [aligned[position].neighbour for position in group]
    return Run(
        song_id=song_id,
        offset_seconds=offset_seconds,
        first_window=group[0],
        last_window=group[-1],
        upload=Span(windows[group[0]].start, windows[group[-1]].end),
        original=Span(min(item.start for item in chosen), max(item.end for item in chosen)),
        similarities=tuple(item.similarity for item in chosen),
    )


def _match_order(match: Match) -> tuple[float, int, int]:
    return (-match.score, match.run.song_id, match.run.first_window)
